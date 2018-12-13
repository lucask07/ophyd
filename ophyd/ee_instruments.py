# Lucas J. Koerner
# 05/2018
# koerner.lucas@stthomas.edu
# University of St. Thomas

# standard library imports 
import functools

# imports that may require package installation
import numpy as np
import scipy.signal as signal

from instrbuilder.config import data_save
from ophyd.scpi_like import ScpiSignal, ScpiSignalBase, ScpiSignalFileSave, StatCalculator
from ophyd import Device, Component, Signal
from ophyd.device import Kind

import scpi  # check if instance is a member of this class
import ic
import instruments

class BlankCommHandle:
    def __init__(self):
        self.write = None
        self.ask = None


def create_filter(order, sample_rate, tau):
    cutoff_freq = 1 / (2 * np.pi * tau)
    norm_cutoff_freq = cutoff_freq / (sample_rate / 2)  # [from 0 - 1]

    num, denom = signal.iirfilter(N=order, Wn=norm_cutoff_freq,
                                  rp=None, rs=None, btype='lowpass', analog=False,
                                  ftype='butter', output='ba')
    return num, denom


def apply_filter(arr, num, denom, sample_rate, tau, final_stat_function):
    output_signal = signal.filtfilt(num, denom, arr)

    tau_settle = 5
    settle_idx = int(tau_settle * tau / (1 / sample_rate))
    decimate_length = int(tau / (1 / sample_rate))

    arr_downsample = output_signal[settle_idx::decimate_length]
    # print('Filter data length after decimation ={}'.format(len(arr_downsample)))
    return final_stat_function(arr_downsample)


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
                                          stat_func=func, kind=Kind.hinted, precision=5)

    locals().update(components)

    def __init__(self, array_source, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for func in self.func_list:
            getattr(self, func.__name__)._img = array_source.get_array
            # update the name
            getattr(self, func.__name__).name = array_source.name + getattr(self, func.__name__).name


class FilterStatistics(Device):

    # TODO: How to not re-run the filter for each statistic,
    #       by re-assigning _img below?

    components = {}

    # use functools.partial to input all parameters but the data array
    #   generate the filter numerator and denominator here

    order = 1  # db/octave = order*6dB
    sample_rate = 400e3/64/16*8  # with on-board oscillator
    tau = 10e-3  # consistent with SR810

    func_list = ['filter_6dB', 'filter_24dB']

    num, denom = create_filter(order=order, sample_rate=sample_rate, tau=tau)
    func_name = 'filter_6dB'

    stat_funcs = [np.mean, np.std]
    for stat_func in stat_funcs:
        func = functools.partial(apply_filter, num=num, denom=denom, sample_rate=sample_rate,
                                 tau=tau, final_stat_function=stat_func)
        components[func_name + '_' + stat_func.__name__] = Component(StatCalculator, name=func, img=None,
                                                                     stat_func=func, kind=Kind.hinted, precision=5)

    order = 4  # db/octave = order*24dB
    num, denom = create_filter(order=order, sample_rate=sample_rate, tau=tau)
    func_name = 'filter_24dB'
    for stat_func in stat_funcs:
        func = functools.partial(apply_filter, num=num, denom=denom, sample_rate=sample_rate,
                                 tau=tau, final_stat_function=stat_func)
        components[func_name + '_' + stat_func.__name__] = Component(StatCalculator, name=func, img=None,
                                                                     stat_func=func, kind=Kind.hinted, precision=5)
    locals().update(components)

    def __init__(self, array_source, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for func in self.func_list:
            for stat_func in self.stat_funcs:
                getattr(self, func + '_' + stat_func.__name__)._img = array_source.get_array
                # update the name
                getattr(self, func + '_' + stat_func.__name__).name = array_source.name + getattr(self, func + '_' +  stat_func.__name__).name


def generate_ophyd_obj(name, scpi_obj):
    components = {}
    for cmd_key, cmd in scpi_obj._cmds.items():
        if cmd.is_config:
            comp_kind = Kind.config
        else:
            comp_kind = Kind.normal

        if isinstance(scpi_obj, scpi.SCPI):
            if hasattr(cmd.getter_type, 'returns_array'):
                if cmd.name == 'burst_volt':
                    components[cmd.name] = Component(ScpiSignalFileSave, name=cmd.name,
                                                     control_layer=scpi_obj, cmd_name=cmd.name,
                                                     save_path=data_save.directory,
                                                     kind=Kind.normal,
                                                     precision=10,  # this precision won't print the full file name,
                                                                    # but enough to be unique
                                                     configs={'reads_per_trigger': 1024, 'aperture': 20e-6,
                                                              'trig_source': 'EXT', 'trig_count': 1})

                if cmd.name == 'burst_volt_timer':
                    components[cmd.name] = Component(ScpiSignalFileSave, name=cmd.name,
                                                     control_layer=scpi_obj, cmd_name=cmd.name,
                                                     save_path=data_save.directory,
                                                     kind=Kind.normal,
                                                     precision=10,  # this precision won't print the full file name,
                                                                    # but enough to be unique
                                                     configs={'reads_per_trigger': 8, 'aperture': 20e-6,
                                                              'trig_source': 'EXT', 'trig_count': 2048,
                                                              'sample_timer': 320e-6, 'repeats': 1})
                                                              # 'sample_timer': 102.4e-6, 'repeats': 1})
                if cmd.getter_type.returns_array:
                    print(
                        'Skipping command {}. Returns an array but a status monitor dictionary is not prepared'.format(
                            cmd.name))
            else:
                if cmd.setter and cmd.getter_inputs == 0 and cmd.setter_inputs < 2:  # a setter
                    components[cmd.name] = Component(ScpiSignal, control_layer=scpi_obj, cmd_name=cmd.name,
                                                     configs={}, kind=comp_kind)
                if (not cmd.setter) and cmd.getter_inputs == 0:  # a getter (only)
                    components[cmd.name] = Component(ScpiSignalBase, control_layer=scpi_obj, cmd_name=cmd.name,
                                                     configs={}, kind=comp_kind)

                #  -----------------------  Multimeter  -----------------------
                if isinstance(scpi_obj, instruments.KeysightMultimeter):
                    # AC/DC configurations.
                    #   Create DC versions
                    if cmd.setter and cmd.getter_inputs == 1 and cmd.setter_inputs == 2 and '{ac_dc}' in cmd.ascii_str:
                        components[cmd.name + '_dc'] = Component(ScpiSignal, control_layer=scpi_obj, cmd_name=cmd.name,
                                                                 configs={'ac_dc': 'DC'}, kind=comp_kind)
                    if (not cmd.setter) and cmd.getter_inputs == 1 and '{ac_dc}' in cmd.ascii_str:
                        components[cmd.name + '_dc'] = Component(ScpiSignalBase, control_layer=scpi_obj, cmd_name=cmd.name,
                                                                 configs={'ac_dc': 'DC'}, kind=comp_kind)
                    # AC/DC configurations.
                    #   Create AC versions
                    if cmd.setter and cmd.getter_inputs == 1 and cmd.setter_inputs == 2 and '{ac_dc}' in cmd.ascii_str:
                        components[cmd.name + '_ac'] = Component(ScpiSignal, control_layer=scpi_obj, cmd_name=cmd.name,
                                                                 configs={'ac_dc': 'AC'}, kind=comp_kind)
                    if (not cmd.setter) and cmd.getter_inputs == 1 and '{ac_dc}' in cmd.ascii_str:
                        components[cmd.name + '_ac'] = Component(ScpiSignalBase, control_layer=scpi_obj, cmd_name=cmd.name,
                                                                 configs={'ac_dc': 'AC'}, kind=comp_kind)

                #  -----------------------  PowerSupply  --------------
                if isinstance(scpi_obj, instruments.RigolPowerSupply):
                    #   Create components per chanel
                    for chan in scpi_obj._channels:
                        if cmd.setter and cmd.getter_inputs == 1 and cmd.setter_inputs == 2 and '{chan}' in cmd.ascii_str:
                            components[cmd.name + '_chan{}'.format(chan)] = Component(ScpiSignal, control_layer=scpi_obj, cmd_name=cmd.name,
                                                                                      configs={'chan': chan}, kind=comp_kind)
                        if (not cmd.setter) and cmd.getter_inputs == 1 and '{channel}' in cmd.ascii_str:
                            components[cmd.name + '_chan{}'.format(chan)] = Component(ScpiSignalBase, control_layer=scpi_obj, cmd_name=cmd.name,
                                                                                      configs={'chan': chan}, kind=comp_kind)
                #  -----------------------  SRSLockIn LockIn  --------------
                if isinstance(scpi_obj, instruments.SRSLockIn):
                    # Create components for long SCPI commands, those that need configuration inputs
                    components['off_exp'] = Component(ScpiSignal,
                                                      control_layer=scpi_obj, cmd_name='off_exp',
                                                      configs={'chan': 2})  # offset and expand

                    components['ch1_disp'] = Component(ScpiSignal,
                                                       control_layer=scpi_obj, cmd_name='ch1_disp',
                                                       configs={'ratio': 0})  # ratio the display to None (0), Aux1 (1) or Aux2 (2)


                #  -----------------------  Oscilloscope  -----------------------
                if isinstance(scpi_obj, instruments.KeysightOscilloscope):
                    if hasattr(cmd.getter_type, 'returns_array'):
                        if cmd.name == 'display_data':
                            print('Creating display data command')

                            def save_png(filename, data):
                                with open(filename, 'wb') as out_f:
                                    out_f.write(bytearray(data))

                            components[cmd.name] = Component(ScpiSignalFileSave, name=cmd.name,
                                                             control_layer=scpi_obj, cmd_name=cmd.name,
                                                             save_path=data_save.directory,
                                                             save_func=save_png, save_spec='PNG', save_ext='png',
                                                             kind=Kind.normal,
                                                             precision=10)  # this precision won't print the full file name, but enough to be unique

                        elif cmd.getter_type.returns_array:
                            print('Skipping Oscilloscpe command {}.'.format(cmd.name))
                            print(' Returns an array but a status monitor dictionary is not prepared')

                    else:
                        if cmd.setter and cmd.getter_inputs == 0 and cmd.setter_inputs < 2:
                            components[cmd.name] = Component(ScpiSignal, control_layer=scpi_obj, cmd_name=cmd.name,
                                                             configs={}, kind=comp_kind)
                        if (not cmd.setter) and cmd.getter_inputs == 0:
                            components[cmd.name] = Component(ScpiSignalBase, control_layer=scpi_obj, cmd_name=cmd.name,
                                                             configs={}, kind=comp_kind)

                    #   Create components per chanel
                    channels = [1, 2, 3, 4]
                    for chan in channels:
                        if cmd.setter and cmd.getter_inputs == 1 and cmd.setter_inputs == 2 and '{chan}' in cmd.ascii_str:
                            components[cmd.name + '_chan{}'.format(chan)] = Component(ScpiSignal, control_layer=scpi_obj, cmd_name=cmd.name,
                                                                                      configs={'chan': chan}, kind=comp_kind)
                        if (not cmd.setter) and cmd.getter_inputs == 1 and '{chan}' in cmd.ascii_str:
                            components[cmd.name + '_chan{}'.format(chan)] = Component(ScpiSignalBase, control_layer=scpi_obj, cmd_name=cmd.name,
                                                                                      configs={'chan': chan}, kind=comp_kind)

                    if cmd.name == 'meas_phase':  # requires two channels to find phase difference
                        components[cmd.name] = Component(ScpiSignalBase, control_layer=scpi_obj, cmd_name=cmd.name,
                                                         configs={'chan1': 1, 'chan2': 2}, kind=comp_kind)


        elif isinstance(scpi_obj, ic.IC):
            if cmd.read_write in ['R/W', 'W']:
                components[cmd.name] = Component(ScpiSignal, control_layer=scpi_obj, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)
            elif cmd.read_write in ['R']:
                components[cmd.name] = Component(ScpiSignal, control_layer=scpi_obj, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)
        else:
            print('unexpected Class type')

    components['unconnected'] = scpi_obj.unconnected

    # create device subclass using type
    ophyd_dev = type(name, (Device,), components)

    # return components for now as a debug hook.
    return ophyd_dev, components


# ------------------------------------------------------------
# 					Oscilloscope
# ------------------------------------------------------------


# class Oscilloscope(Device):
#     components = {}
#     for cmd_key, cmd in scpi_osc._cmds.items():
#         if cmd.is_config:
#             comp_kind = Kind.config
#         else:
#             comp_kind = Kind.normal
#
#         if hasattr(cmd.getter_type, 'returns_array'):
#             if cmd.name == 'display_data':
#                 print('Creating display data command')
#
#                 def save_png(filename, data):
#                     with open(filename, 'wb') as out_f:
#                         out_f.write(bytearray(data))
#
#                 components[cmd.name] = Component(ScpiSignalFileSave, name=cmd.name,
#                                                  scpi_cl=scpi_osc, cmd_name=cmd.name,
#                                                  save_path=data_save.directory,
#                                                  save_func=save_png, save_spec='PNG', save_ext='png',
#                                                  kind=Kind.normal,
#                                                  precision=10)  # this precision won't print the full file name, but enough to be unique)
#
#             elif cmd.getter_type.returns_array:
#                 print('Skipping Oscilloscpe command {}.'.format(cmd.name))
#                 print(' Returns an array but a status monitor dictionary is not prepared')
#
#         else:
#             if cmd.setter and cmd.getter_inputs == 0 and cmd.setter_inputs < 2:
#                 components[cmd.name] = Component(ScpiSignal, scpi_cl=scpi_osc, cmd_name=cmd.name,
#                                                  configs={}, kind=comp_kind)
#             if (not cmd.setter) and cmd.getter_inputs == 0:
#                 components[cmd.name] = Component(ScpiSignalBase, scpi_cl=scpi_osc, cmd_name=cmd.name,
#                                                  configs={}, kind=comp_kind)
#
#         #   Create components per chanel
#         channels = [1, 2, 3, 4]
#         for chan in channels:
#             if cmd.setter and cmd.getter_inputs == 1 and cmd.setter_inputs == 2 and '{chan}' in cmd.ascii_str:
#                 components[cmd.name + '_chan{}'.format(chan)] = Component(ScpiSignal, scpi_cl=scpi_osc, cmd_name=cmd.name,
#                                                  configs={'chan': channel}, kind=comp_kind)
#             if (not cmd.setter) and cmd.getter_inputs == 1 and '{channel}' in cmd.ascii_str:
#                 components[cmd.name + '_chan{}'.format(chan)] = Component(ScpiSignalBase, scpi_cl=scpi_osc, cmd_name=cmd.name,
#                                                  configs={'chan': channel}, kind=comp_kind)
#
#         if cmd.name == 'meas_phase':  # requires two channels to find phase difference
#             components[cmd.name] = Component(ScpiSignalBase, scpi_cl=scpi_osc, cmd_name=cmd.name,
#                                              configs={'chan1': 1, 'chan2': 2}, kind=comp_kind)
#
#     unconnected = scpi_osc.unconnected
#     locals().update(components)
#
#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#         self.help = scpi_osc.help
#         self.help_all = scpi_osc.help_all
