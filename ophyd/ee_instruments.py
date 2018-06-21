# Lucas J. Koerner
# 05/2018
# koerner.lucas@stthomas.edu
# University of St. Thomas

# standard library imports 
import sys
import numpy as np

'''
Big issue on the June 15.
If a component is hinted it will be read at the start of the scan for metadata. 
This doesn't trigger the signal 
so if it needs to be triggered be careful
'''

# use symbolic links
sys.path.append(
    '/Users/koer2434/ophyd/')  # these 2 will become an import of ophyd
sys.path.append(
    '/Users/koer2434/instrbuilder/')  # this instrbuilder: the SCPI library


# imports that require sys.path.append pointers
from instrbuilder.setup import scpi_lia, scpi_fg, data_save
from ophyd.scpi import ScpiSignal, ScpiSignalBase, ScpiSignalFileSave, StatCalculator
from ophyd import Device, Component
from ophyd.device import Kind

# ------------------------------------------------------------
#					Lock-in Amplifier
# ------------------------------------------------------------

class LockInAuto(Device):
    components = {}
    for cmd_key, cmd in scpi_lia._cmds.items():
        if cmd.is_config:
            comp_kind = Kind.config
        else:
            comp_kind = Kind.normal

        if hasattr(cmd.getter_type, 'returns_array'):
            if cmd.getter_type.returns_array:
                if cmd_key == 'read_buffer':
                    # setup, monitoring and wind-down for the read_buffer command.
                    # TODO: make this less awkward, part of instrbuilder?
                    status_monitor = {'name': 'data_pts_ready', 'configs': {},
                                      'threshold_function': lambda read_val, thresh: read_val > thresh,
                                      'threshold_level': 40,
                                      'poll_time': 0.05,
                                      'trig_name': ['reset_scan', 'start_scan', 'trig'],
                                      'trig_configs': {},
                                      'post_name': 'pause_scan',
                                      'post_configs': {}}
                    components[cmd.name] = Component(ScpiSignalFileSave, name=cmd.name,
                                            scpi_cl=scpi_lia, cmd_name=cmd.name,
                                            save_path = data_save.directory,
                                            kind = Kind.normal,
                                            precision = 10, # this precision won't print the full file name, but enough to be unique
                                            configs = {'start_pt': 0, 'num_pts' : 20},
                                                     status_monitor=status_monitor)
                else:
                    print('Skipping LockIn command {}. Returns an array but a status monitor dictionary is not prepared'.format(cmd.name))

        else:
            if cmd.setter and cmd.getter_inputs == 0 and cmd.setter_inputs < 2:
                components[cmd.name] = Component(ScpiSignal, scpi_cl=scpi_lia, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)
            if (not cmd.setter) and cmd.getter_inputs == 0:
                components[cmd.name] = Component(ScpiSignalBase, scpi_cl=scpi_lia, cmd_name=cmd.name,
                                                  configs={}, kind=comp_kind)

    # Other commands need to be explicitly entered
    #  statistics calculated on the array
    isum = Component(StatCalculator, name='sum', img=None,
                     stat_func=np.sum, kind=Kind.hinted)
    istd = Component(StatCalculator, name='std', img=None,
                     stat_func=np.std, kind=Kind.hinted)
    # Example of a long setter, i.e. a SCPI command that takes more than a single value
    off_exp = ScpiSignal(
        scpi_cl=scpi_lia, cmd_name='off_exp', configs={'chan':
                                                           2})  # offset and expand
    unconnected = scpi_lia.unconnected

    locals().update(components)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # TODO: Awkward, find better way attach statistics to the read_buffer command
        self.isum._img = self.read_buffer
        self.istd._img = self.read_buffer


class FunctionGenAuto(Device):
    components = {}
    for cmd_key, cmd in scpi_fg._cmds.items():
        if cmd.is_config:
            comp_kind = Kind.config
        else:
            comp_kind = Kind.normal

        if hasattr(cmd.getter_type, 'returns_array'):
            if cmd.getter_type.returns_array:
                print('Skipping FunctionGen command {}. Returns an array but a status monitor dictionary is not prepared'.format(cmd.name))
        else:
            if cmd.setter and cmd.getter_inputs == 0 and cmd.setter_inputs < 2:
                components[cmd.name] = Component(ScpiSignal, scpi_cl=scpi_fg, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)
            if (not cmd.setter) and cmd.getter_inputs == 0:
                components[cmd.name] = Component(ScpiSignalBase, scpi_cl=scpi_fg, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)
    unconnected = scpi_fg.unconnected
    locals().update(components)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)