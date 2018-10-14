# vi: ts=4 sw=4
import logging
import time
import threading
import functools 
from collections import deque
import os
import itertools

import numpy as np
import wrapt

from .status import Status
from .signal import Signal
from .sim import SynSignal, NullStatus, new_uid

logger = logging.getLogger(__name__)


class ScpiSignalBase(Signal):
    """A read-only SCPI signal -- that is, one without setters

    Keyword arguments are passed on to the base class (Signal) initializer

    Parameters
    ----------
    scpi_cl : 
        The instrument control layer object which has write, ask, and a dictionary of commands (_cmds) 
    cmd_name : str
        The name of the command to read from [_cmds]
    name 
    configs : dict, optional 
        The configuration dictionary that is sent to get if its a long getter

    """
    def __init__(self, *, scpi_cl, cmd_name, name=None,
                 precision=7, configs={}, dtype='number',
                 shape=1, status_monitor=None,
                 **kwargs):

        cmd = scpi_cl._cmds[cmd_name]

        self._scpi_cl = scpi_cl 
        composite_name = scpi_cl.name + '_' + cmd_name
        super().__init__(name=composite_name, **kwargs)
        self._read_name = composite_name
        self.lookup = cmd.lookup
        self.is_config = cmd.is_config
        self.precision = precision
        self.doc = cmd.doc
        self.dtype = dtype
        self.shape = shape
        self.delay = None

        # TODO: limits 
        # if scpi_cl._cmds[cmd_name].limits is not None:
        #     self.scpi_limits = tuple(scpi_cl._cmds[cmd_name].limits)
        # else: 
        #     self.scpi_limits = self.limits

        # TODO: Instrbuilder will use a list of "limits" 
        #       in this case (when len(limits) > 2 or type(limits) is Str) 
        #       the value set has to be a member of this list.
        #       Does this break bluesky?

        # TODO: my assumption is that the ophyd 'enum_strs' is the same as the 
        #       instrbuilder lookup tables. Confirm this is correct.
        self.enum_strs = list(scpi_cl._cmds[cmd.name].lookup.keys())

        # setup the getter 
        if scpi_cl._cmds[cmd.name].returns_image: #TODO: determine if this is used
            @wrapt.decorator
            def only_one_return(wrapped, instance, args, kwargs):
                return wrapped(*args, **kwargs)[0]
            _get = functools.partial(scpi_cl.get, name=cmd.name, configs=configs)
        else:
            _get = functools.partial(scpi_cl.get, name=cmd.name, configs=configs)
        self.get = _get

        # TODO -- better way to do this? 
        # setup the setter in case this signal is a setter (will be converted to self.set in the subclass)
        self._set = functools.partial(scpi_cl.set, name=cmd.name, configs=configs)

        self._status_monitor = status_monitor 
        if status_monitor is not None:
            def trig_func():
                for tname in status_monitor['trig_name']:
                    scpi_cl.set(None, name=tname,
                        configs=status_monitor['trig_configs'])

            self._trigger_func = trig_func
            self._status_read = functools.partial(scpi_cl.get, name=status_monitor['name'],
                configs=status_monitor['configs'])
            self._threshold_function = status_monitor['threshold_function']
            self._threshold_level = status_monitor['threshold_level']
            self._poll_time = status_monitor['poll_time']
            self._post_status = functools.partial(scpi_cl.set, name=status_monitor['post_name'],
                configs=status_monitor['post_configs'])

    def trigger(self):
        # first wait until another signal is ready, i.e. a count of readings in a buffer
        if self._status_monitor is not None:
            self._trigger_func()
            while not (self._threshold_function(self._status_read(), self._threshold_level)):
                time.sleep(self._poll_time)
            self._post_status(None)
        # now trigger
        super().trigger()
        return NullStatus()

    def _repr_info(self):
        yield ('read_name', self._read_name)
        yield from super()._repr_info()

    def describe(self):
        """Return the description as a dictionary

        Returns
        -------
        dict
            Dictionary of name and formatted description string
        """
        desc = {'source': '{}:{}'.format(self._scpi_cl.name, self._read_name), }

        desc['dtype'] = self.dtype
        desc['shape'] = self.shape

        try:
            desc['precision'] = int(self.precision)
        except (ValueError, TypeError):
            pass

        low_limit, high_limit = self.limits
        desc['lower_ctrl_limit'] = low_limit
        desc['upper_ctrl_limit'] = high_limit

        if self.enum_strs:
            desc['enum_strs'] = self.enum_strs

        return {self.name: desc}

    def read(self):
        """Read the signal and format for data collection

        Returns
        -------
        dict
            Dictionary of value timestamp pairs
        """
        return {self.name: {'value': self.value,
                            'timestamp': self.timestamp}}

    # TODO: limits 
    # @property
    # def limits(self):
    #     return self.scpi_limits


class ScpiSignal(ScpiSignalBase):
    """A read-write SCPI signal -- that is, with a setter and a getter

    Keyword arguments are passed on to the base class (Signal) initializer

    Parameters
    ----------
    scpi_cl : 
        The instrument control layer object which has write, ask, and a dictionary of commands (_cmds) 
    name : str
        The name of the command to read from [_cmds]
    configs : dict, optional 
        The configuration dictionary that is sent to get if its a long getter
    """
    def set(self, value):        
        st = Status()

        def check_return(ret):
            if ret[0]:
                st._finished()

        if self.delay:
            def sleep_and_finish():
                ret = self._set(value=value)
                time.sleep(self.delay)  # in sim.py time is imported as ttime, not sure why
                check_return(ret)

            threading.Thread(target=sleep_and_finish, daemon=True).start()

        else:
            ret = self._set(value=value)
            check_return(ret)

        return st


class ScpiCompositeBase(Signal):
    """A read-only SCPI signal that originates from a composite function of multiple SCPI actions

    Keyword arguments are passed on to the base class (Signal) initializer

    Parameters
    ----------
    get_func :
        The command to read a value
    name : str
        The name of the command
    configs : dict, optional
        The configuration dictionary that is sent to get if its a long getter

    """
    def __init__(self, *, get_func, name,
                precision = 7, configs = {}, dtype = 'number',
                shape = 1, status_monitor = None,
                **kwargs):

        self._read_name = name
        super().__init__(name = self._read_name, **kwargs)

        self.is_config = False
        self.precision = precision
        self.dtype = dtype
        self.shape = shape
        self.delay = None

        # TODO: limits
        # if scpi_cl._cmds[cmd_name].limits is not None:
        #     self.scpi_limits = tuple(scpi_cl._cmds[cmd_name].limits)
        # else:
        #     self.scpi_limits = self.limits

        # TODO: Instrbuilder will use a list of "limits"
        #       in this case (when len(limits) > 2 or type(limits) is Str)
        #       the value set has to be a member of this list.
        #       Does this break bluesky?

        # TODO: my assumption is that the ophyd 'enum_strs' is the same as the
        #       instrbuilder lookup tables. Confirm this is correct.
        # self.enum_strs = list(scpi_cl._cmds[cmd.name].lookup.keys())

        self._get = get_func
        self.get = self._get

        # TODO -- better way to do this?
        # setup the setter in case this signal is a setter (will be converted to self.set in the subclass)

        self._status_monitor = status_monitor
        if status_monitor is not None:
            print('Status monitor is not implemented for SCPI overrides')

    def trigger(self):
        super().trigger()
        return NullStatus()

    def _repr_info(self):
        yield ('read_name', self._read_name)
        yield from super()._repr_info()

    def describe(self):
        """Return the description as a dictionary

        Returns
        -------
        dict
            Dictionary of name and formatted description string
        """
        desc = {'source': '{}:{}'.format(self._scpi_cl.name, self._read_name), }

        val = self.value
        desc['dtype'] = self.dtype
        desc['shape'] = self.shape

        try:
            desc['precision'] = int(self.precision)
        except (ValueError, TypeError):
            pass

        low_limit, high_limit = self.limits
        desc['lower_ctrl_limit'] = low_limit
        desc['upper_ctrl_limit'] = high_limit

        if self.enum_strs:
            desc['enum_strs'] = self.enum_strs

        return {self.name: desc}

    def read(self):
        """Read the signal and format for data collection

        Returns
        -------
        dict
            Dictionary of value timestamp pairs
        """

        return {self.name: {'value': self.get(),
                            'timestamp': self.timestamp}}

    # TODO: limits
    # @property
    # def limits(self):
    #     return self.scpi_limits


class ScpiCompositeSignal(ScpiCompositeBase):
    """A read-write SCPI signal that originates from a composite function of multiple SCPI actions


    Keyword arguments are passed on to the base class (Signal) initializer

    Parameters
    ----------
    get_func :
        The command to read a value
    name : str
        The name of the command
    set_func:
        The command for setting
    configs : dict, optional
        The configuration dictionary that is sent to get if its a long getter
    """

    def __init__(self, *, get_func, name, set_func,
                precision = 7, configs = {}, dtype = 'number',
                shape = 1, status_monitor = None,
                **kwargs):
        self._set = set_func
        super().__init__(get_func=get_func, name=name, configs=configs)

    def set(self, value):
        st = Status()

        def check_return(ret):
            if ret[0]:
                st._finished()

        if self.delay:
            def sleep_and_finish():
                time.sleep(self.delay) # in sim.py time is imported as ttime, not sure why
                ret = self._set(value=value)
                check_return(ret)

            threading.Thread(target=sleep_and_finish, daemon=True).start()

        else:
            ret = self._set(value=value)
            check_return(ret)

        return st


class ScpiSignalFileSave(ScpiSignalBase):
    """
    A ScpiSignalBase (read-only) integrated with databroker.assets

    Parameters
    ----------
    name : string, keyword only
    parent : Device, optional
        Used internally if this Signal is made part of a larger Device.
    loop : asyncio.EventLoop, optional
        used for ``subscribe`` updates; uses ``asyncio.get_event_loop()`` if
        unspecified
    save_path : str, optional
        Path to save files to, if None make a temp dir, defaults to None.
    save_func : function, optional
        The function to save the data, function signature must be:
        `func(file_path, array)`, defaults to np.save
    save_spec : str, optional
        The spec for the save function, defaults to 'RWFS_NPY'
    save_ext : str, optional
        The extension to add to the file name, defaults to '.npy'
    precision : integer, optional
        Precision that will be used when printing the file name
    dtype : 'string', optional
        The data-type for live display callbacks 
    """

    def __init__(self, *args, save_path=None,
                 save_func=np.save, save_spec='NPY_SEQ', save_ext='npy',
                 dtype = 'string', precision = 80,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.save_func = save_func
        self.save_ext = save_ext
        self._resource_uid = None
        self._datum_counter = None
        self._asset_docs_cache = deque()
        if save_path is None:
            self.save_path = mkdtemp()
        else:
            self.save_path = save_path
        self._spec = save_spec  # spec name stored in resource doc

        self._file_stem = None
        self._path_stem = None
        self._result = {}
        self._value = None # where we hold the image in memory so that a stats module 
                           # can do calculations 
        self.dtype = dtype
        self.precision = precision

    def stage(self):
        self._resource_uid = new_uid()
        # previous SynSignalWithRegistry used a full uid for the resource labeling and 
        #  a separate short uid for the saved file; this is confusing -- use the uid for both 
        self._file_stem = self._resource_uid 
        self._path_stem = os.path.join(self.save_path, self._file_stem)
        self._datum_counter = itertools.count()

        resource = {'spec': self._spec,
                    'root': self.save_path,
                    'resource_path': self._file_stem,
                    'resource_kwargs': {},
                    'path_semantics': os.name}

        resource['uid'] = self._resource_uid
        self._asset_docs_cache.append(('resource', resource))

    def trigger(self):
        super().trigger()
        # save file stash file name
        self._result.clear()
        for idx, (name, reading) in enumerate(super().read().items()):

            datum_cnt = next(self._datum_counter)

            # Save the actual reading['value'] to disk. 
            # Instrbuilder pulls the value into memory, ophyd saves it to disk
            self.save_func('{}_{}_{}.{}'.format(self._path_stem, idx, datum_cnt,
                                             self.save_ext), reading['value'])
            datum = {'resource': self._resource_uid,
                     'datum_kwargs': dict(index=idx)}

            # We need to generate the datum_id.
            datum_id = '{}_{}_{}.{}'.format(self._resource_uid, idx,
                                          datum_cnt, self.save_ext)
            datum['datum_id'] = datum_id
            self._asset_docs_cache.append(('datum', datum))
            # And now change the reading in place, replacing the value with
            # a reference to Registry.
            # but first store a copy of the "image"
            self._value = reading['value']
            reading['value'] = datum_id
            self._result[name] = reading

        return NullStatus()

    def read(self):
        # The "value" read is the filename; this filename will be put into the
        # sqlite database generated by bluesky, but the entire "image" will not be
        return self._result

    def get_array(self):
        return self._value

    def describe(self):
        res = super().describe()
        for key in res:
            res[key]['external'] = "FILESTORE"
            res[key]['dtype'] = self.dtype
            res[key]['precision'] = self.precision
        return res

    def collect_asset_docs(self):
        items = list(self._asset_docs_cache)
        self._asset_docs_cache.clear()
        for item in items:
            yield item

    def unstage(self):
        self._resource_uid = None
        self._datum_counter = None
        self._asset_docs_cache.clear()
        self._file_stem = None
        self._path_stem = None
        self._result.clear()


class StatCalculator(SynSignal):
    """
    Evaluate a statistic from a Device that produces a 1D or 2D np.array
        (e.g. an imaging detector) [derived from SynGauss]

    Parameters
    ----------
    name : string
    img : Device
        device that captures an array and stores it in memory to ._value
        as an np.array 
    signal_name : string 
        name of the signal where the value is stored (e.g 'cam_img')
    stat_func : callable
        For example: np.mean

    Example
    -------

    """

    def __init__(self, name, stat_func, img = None, **kwargs):
        self._img = img

        def func():
            if self._img is not None:
                m = self._img()        # the actual numeric value of the image is "hidden",
                                           # not accessible by read()
                                           # since all we want the bluesky RE to see is the UID/filename
                if m is None:
                    return None
                else:
                    return stat_func(m)
            else:
                return None

        super().__init__(func=func, name=name, **kwargs)
