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
from instrbuilder.setup import scpi_lia, scpi_fg, scpi_dmm, scpi_osc, data_save
from ophyd.scpi import ScpiSignal, ScpiSignalBase, ScpiSignalFileSave, StatCalculator, ScpiCompositeBase, ScpiCompositeSignal
from ophyd import Device, Component, Signal
from ophyd.device import Kind


class ManualDevice(Device):
    val = Component(Signal, name='val')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class BasicStatistics(Device):
    func_list = [np.sum, np.mean, np.std, np.min, np.max, len]
    components = {}

    for func in func_list:
        func_name = func.__name__
        components[func_name] = Component(StatCalculator, name=func_name, img=None,
                                          stat_func=func, kind=Kind.hinted)

    locals().update(components)

    def __init__(self, array_source, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for func in self.func_list:
            getattr(self, func.__name__)._img = array_source.get_array
            # update the name
            getattr(self, func.__name__).name = array_source.name + getattr(self, func.__name__).name

# ------------------------------------------------------------
# 					Lock-in Amplifier
# ------------------------------------------------------------


class LockIn(Device):
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
                                      'threshold_level': 100,
                                      'poll_time': 0.05,
                                      'trig_name': ['reset_scan', 'start_scan', 'trig'],
                                      'trig_configs': {},
                                      'post_name': 'pause_scan',
                                      'post_configs': {}}
                    components[cmd.name] = Component(ScpiSignalFileSave, name=cmd.name,
                                                     scpi_cl=scpi_lia, cmd_name=cmd.name,
                                                     save_path = data_save.directory,
                                                     kind = Kind.normal,
                                                     precision = 10, # precision sets length printed in live table
                                                     configs = {'start_pt': 0, 'num_pts': 80},
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
    # Long setters (SCPI commands that takes more than a single value)

    off_exp = Component(ScpiSignal,
                        scpi_cl=scpi_lia, cmd_name='off_exp',
                        configs={'chan': 2})  # offset and expand

    ch1_disp = Component(ScpiSignal,
                         scpi_cl=scpi_lia, cmd_name='ch1_disp',
                         configs={'ratio': 0})  # ratio the display to None (0), Aux1 (1) or Aux2 (2)

    test_composite = Component(ScpiCompositeBase,
                               get_func=scpi_lia.test_composite_get,
                               name='fg:f_mult_and_get')

    composite_set = Component(ScpiCompositeSignal,
                              get_func=None,
                              set_func = scpi_lia.test_composite_set,
                              name='fg:freq_tau_sens')

    unconnected = scpi_lia.unconnected

    locals().update(components)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.help = scpi_lia.help
        self.help_all = scpi_lia.help_all

    def stage(self):
        super().stage()


# ------------------------------------------------------------
# 					Function Generator
# ------------------------------------------------------------

class FunctionGen(Device):
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
        self.help = scpi_fg.help
        self.help_all = scpi_fg.help_all

    def stage(self):
        # TODO: this is too specific
        self.load.set('INF')
        self.output.set('ON')
        super().stage()

# ------------------------------------------------------------
# 					Oscilloscope
# ------------------------------------------------------------


class Oscilloscope(Device):
    components = {}
    channels = [1,2]
    for cmd_key, cmd in scpi_osc._cmds.items():
        if cmd.is_config:
            comp_kind = Kind.config
        else:
            comp_kind = Kind.normal

        if hasattr(cmd.getter_type, 'returns_array'):
            if cmd.name == 'display_data':
                print('Creating display data command')

                def save_png(filename, data):
                    with open(filename, 'wb') as out_f:
                        out_f.write(bytearray(data))

                components[cmd.name] = Component(ScpiSignalFileSave, name=cmd.name,
                                                 scpi_cl=scpi_osc, cmd_name=cmd.name,
                                                 save_path=data_save.directory,
                                                 save_func=save_png, save_spec='PNG', save_ext='png',
                                                 kind=Kind.normal,
                                                 precision=10)  # this precision won't print the full file name, but enough to be unique)

            elif cmd.getter_type.returns_array:
                print('Skipping Oscilloscpe command {}.'.format(cmd.name))
                print(' Returns an array but a status monitor dictionary is not prepared')

        else:
            if cmd.setter and cmd.getter_inputs == 0 and cmd.setter_inputs < 2:
                components[cmd.name] = Component(ScpiSignal, scpi_cl=scpi_osc, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)
            if (not cmd.setter) and cmd.getter_inputs == 0:
                components[cmd.name] = Component(ScpiSignalBase, scpi_cl=scpi_osc, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)

        #   Create components per chanel
        for channel in channels:
            if cmd.setter and cmd.getter_inputs == 1 and cmd.setter_inputs == 2 and '{channel}' in cmd.ascii_str:
                components[cmd.name + '_chan{}'.format(channel)] = Component(ScpiSignal, scpi_cl=scpi_osc, cmd_name=cmd.name,
                                                 configs={'channel': channel}, kind=comp_kind)
            if (not cmd.setter) and cmd.getter_inputs == 1 and '{channel}' in cmd.ascii_str:
                components[cmd.name + '_chan{}'.format(channel)] = Component(ScpiSignalBase, scpi_cl=scpi_osc, cmd_name=cmd.name,
                                                 configs={'channel': channel}, kind=comp_kind)


    unconnected = scpi_osc.unconnected
    locals().update(components)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.help = scpi_osc.help
        self.help_all = scpi_osc.help_all

# ------------------------------------------------------------
# 					Digital Multimeter
# ------------------------------------------------------------


class MultiMeter(Device):
    components = {}
    for cmd_key, cmd in scpi_dmm._cmds.items():
        if cmd.is_config:
            comp_kind = Kind.config
        else:
            comp_kind = Kind.normal

        if hasattr(cmd.getter_type, 'returns_array'):
            if cmd.name == 'burst_volt':
                components[cmd.name] = Component(ScpiSignalFileSave, name=cmd.name,
                                                 scpi_cl=scpi_dmm, cmd_name=cmd.name,
                                                 save_path=data_save.directory,
                                                 kind=Kind.normal,
                                                 precision=10, # this precision won't print the full file name, but enough to be unique
                                                 configs={'reads_per_trigger': 1024, 'aperture': 20e-6,
                                                 'trig_source': 'EXT', 'trig_count': 1})

            if cmd.name == 'burst_volt_timer':
                components[cmd.name] = Component(ScpiSignalFileSave, name=cmd.name,
                                                 scpi_cl=scpi_dmm, cmd_name=cmd.name,
                                                 save_path=data_save.directory,
                                                 kind=Kind.normal,
                                                 precision=10,
                                                 # this precision won't print the full file name, but enough to be unique
                                                 configs={'reads_per_trigger': 8, 'aperture': 20e-6,
                                                          'trig_source': 'EXT', 'trig_count': 1024,
                                                          'sample_timer': 102.4e-6, 'repeats': 1})

            elif cmd.getter_type.returns_array:
                print('Skipping command {}. '.format(cmd.name))
                print('Returns an array but a status monitor dictionary is not prepared')
        else:
            if cmd.setter and cmd.getter_inputs == 0 and cmd.setter_inputs < 2:
                components[cmd.name] = Component(ScpiSignal, scpi_cl=scpi_dmm, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)
            if (not cmd.setter) and cmd.getter_inputs == 0:
                components[cmd.name] = Component(ScpiSignalBase, scpi_cl=scpi_dmm, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)
            # AC/DC configurations.
            #   Create DC versions
            if cmd.setter and cmd.getter_inputs == 1 and cmd.setter_inputs == 2 and '{ac_dc}' in cmd.ascii_str:
                components[cmd.name + '_dc'] = Component(ScpiSignal, scpi_cl=scpi_dmm, cmd_name=cmd.name,
                                                 configs={'ac_dc': 'DC'}, kind=comp_kind)
            if (not cmd.setter) and cmd.getter_inputs == 1 and '{ac_dc}' in cmd.ascii_str:
                components[cmd.name + '_dc'] = Component(ScpiSignalBase, scpi_cl=scpi_dmm, cmd_name=cmd.name,
                                                 configs={'ac_dc': 'DC'}, kind=comp_kind)
            # AC/DC configurations.
            #   Create AC versions
            if cmd.setter and cmd.getter_inputs == 1 and cmd.setter_inputs == 2 and '{ac_dc}' in cmd.ascii_str:
                components[cmd.name + '_ac'] = Component(ScpiSignal, scpi_cl=scpi_dmm, cmd_name=cmd.name,
                                                 configs={'ac_dc': 'AC'}, kind=comp_kind)
            if (not cmd.setter) and cmd.getter_inputs == 1 and '{ac_dc}' in cmd.ascii_str:
                components[cmd.name + '_ac'] = Component(ScpiSignalBase, scpi_cl=scpi_dmm, cmd_name=cmd.name,
                                                 configs={'ac_dc': 'AC'}, kind=comp_kind)

    unconnected = scpi_dmm.unconnected
    locals().update(components)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.help = scpi_dmm.help
        self.help_all = scpi_dmm.help_all
