'''
(*)~----------------------------------------------------------------------------------
 Pupil - eye tracking platform
 Copyright (C) 2012-2016  Pupil Labs

 Distributed under the terms of the GNU Lesser General Public License (LGPL v3.0).
 License details are in the file license.txt, distributed as part of this software.
----------------------------------------------------------------------------------~(*)
'''

from .base_backend import InitialisationError, StreamError, Base_Source, Base_Manager

import ndsi, time
assert ndsi.NDS_PROTOCOL_VERSION >= '0.2.13'

import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

class NDSI_Source(Base_Source):
    """Pupil Mobile video source

    Attributes:
        get_frame_timeout (float): Maximal waiting time for next frame
        sensor (ndsi.Sensor): NDSI sensor backend
    """
    def __init__(self, g_pool, frame_size, frame_rate, network=None, source_id=None, host_name=None, sensor_name=None, **settings):
        super(NDSI_Source, self).__init__(g_pool)
        self.sensor = None
        self._source_id = source_id
        self._sensor_name = sensor_name
        self._host_name = host_name
        self._frame_size = frame_size
        self._frame_rate = frame_rate
        self.has_ui = False
        self.control_id_ui_mapping = {}
        self.get_frame_timeout = 50 # ms
        self.ghost_mode_timeout = 10 # sec
        self._initial_refresh = True
        self.last_update = self.g_pool.get_timestamp()

        if not network:
            logger.debug('No network reference provided. Capture is started in ghost mode. No images will be supplied.')
            return

        self.recover(network)

        if not self.sensor or not self.sensor.supports_data_subscription:
            logger.error('Init failed. Capture is started in ghost mode. No images will be supplied.')
            self.cleanup()

        logger.debug('NDSI Source Sensor: %s'%self.sensor)

    def recover(self, network):
        logger.debug('Trying to recover with %s, %s, %s'%(self._source_id,self._sensor_name, self._host_name))
        if self._source_id:
            try:
                # uuid given
                self.sensor = network.sensor(self._source_id, callbacks=(self.on_notification,))
            except ValueError:
                pass

        if self.sensor:
            self._sensor_name = self.sensor.name
            self._host_name = self.sensor.host_name
            return
        if self._host_name and self._sensor_name:
            for sensor in network.sensors.values():
                if (sensor['host_name'] == self._host_name and
                    sensor['sensor_name'] == self._sensor_name):
                    self.sensor = network.sensor(sensor['sensor_uuid'], callbacks=(self.on_notification,))
                    if self.sensor:
                        self._sensor_name = self.sensor.name
                        self._host_name = self.sensor.host_name
                        break
        else:
            for s_id in network.sensors:
                self.sensor = network.sensor(s_id, callbacks=(self.on_notification,))
                if self.sensor:
                    self._sensor_name = self.sensor.name
                    self._host_name = self.sensor.host_name
                    break

    @property
    def name(self):
        return '%s @ %s'%(self._sensor_name, self._host_name)

    def poll_notifications(self):
        while self.sensor.has_notifications:
            self.sensor.handle_notification()

    def recent_events(self,events):
        if self.sensor:
            self.poll_notifications()
            try:
                frame = self.sensor.get_newest_data_frame(timeout=self.get_frame_timeout)
            except ndsi.StreamError:
                frame = None
            except Exception as e:
                frame = None
                import traceback
                logger.error(traceback.format_exc())
            if frame:
                self._recent_frame = frame
                self._frame_size = (frame.width, frame.height)
                self.last_update = self.g_pool.get_timestamp()
                events['frame'] = frame
            elif self.g_pool.get_timestamp() - self.last_update > self.ghost_mode_timeout:
                logger.info('Entering gost mode')
                if self.sensor:
                    self.sensor.unlink()
                self.sensor = None
                self._source_id = None
                self._initial_refresh = True
                self.update_control_menu()
                self.last_update = self.g_pool.get_timestamp()
        else:
            time.sleep(self.get_frame_timeout/1e3)

    def on_notification(self, sensor, event):
        # should only called if sensor was created
        if self._initial_refresh:
            self.sensor.set_control_value('streaming', True)
            self.sensor.refresh_controls()
            self._initial_refresh = False
        if event['subject'] == 'error':
            # if not event['error_str'].startswith('err=-3'):
            logger.warning('Error %s'%event['error_str'])
            if 'control_id' in event and event['control_id'] in self.sensor.controls:
                logger.debug('%s'%self.sensor.controls[event['control_id']])
        elif self.has_ui and (
            event['control_id'] not in self.control_id_ui_mapping or
            event['changes'].get('dtype') == "strmapping" or
            event['changes'].get('dtype') == "intmapping"):
            self.update_control_menu()

    @property
    def frame_size(self):
        return self._frame_size

    @property
    def frame_rate(self):
        return self._frame_rate

    @property
    def jpeg_support(self):
        return True

    def get_init_dict(self):
        settings = super(NDSI_Source, self).get_init_dict()
        settings['name'] = self.name
        settings['frame_rate'] = self.frame_rate
        settings['frame_size'] = self.frame_size
        if self.sensor:
            settings['sensor_name'] = self.sensor.name
            settings['host_name'] = self.sensor.host_name
        else:
            settings['sensor_name'] = self._sensor_name
            settings['host_name'] = self._host_name
        return settings


    def init_gui(self):
        from pyglui import ui
        self.has_ui = True
        self.uvc_menu = ui.Growing_Menu("UVC Controls")
        self.update_control_menu()

    def add_controls_to_menu(self,menu,controls):
        from pyglui import ui
        # closure factory
        def make_value_change_fn(ctrl_id):
            def initiate_value_change(val):
                logger.debug('%s: %s >> %s'%(self.sensor, ctrl_id, val))
                self.sensor.set_control_value(ctrl_id, val)
            return initiate_value_change

        for ctrl_id, ctrl_dict in controls:
            try:
                dtype    = ctrl_dict['dtype']
                ctrl_ui  = None
                if dtype == "string":
                    ctrl_ui = ui.Text_Input(
                        'value',
                        ctrl_dict,
                        label=ctrl_dict['caption'],
                        setter=make_value_change_fn(ctrl_id))
                elif dtype == "integer" or dtype == "float":
                    convert_fn = int if dtype == "integer" else float
                    ctrl_ui = ui.Slider(
                        'value',
                        ctrl_dict,
                        label=ctrl_dict['caption'],
                        min =convert_fn(ctrl_dict.get('min', 0)),
                        max =convert_fn(ctrl_dict.get('max', 100)),
                        step=convert_fn(ctrl_dict.get('res', 0.)),
                        setter=make_value_change_fn(ctrl_id))
                elif dtype == "bool":
                    ctrl_ui = ui.Switch(
                        'value',
                        ctrl_dict,
                        label=ctrl_dict['caption'],
                        on_val=ctrl_dict.get('max',True),
                        off_val=ctrl_dict.get('min',False),
                        setter=make_value_change_fn(ctrl_id))
                elif dtype == "strmapping" or dtype == "intmapping":
                    desc_list = ctrl_dict['map']
                    labels    = [desc['caption'] for desc in desc_list]
                    selection = [desc['value']   for desc in desc_list]
                    ctrl_ui = ui.Selector(
                        'value',
                        ctrl_dict,
                        label=ctrl_dict['caption'],
                        labels=labels,
                        selection=selection,
                        setter=make_value_change_fn(ctrl_id))
                if ctrl_ui:
                    ctrl_ui.read_only = ctrl_dict.get('readonly',False)
                    self.control_id_ui_mapping[ctrl_id] = ctrl_ui
                    menu.append(ctrl_ui)
                else:
                    logger.error('Did not generate UI for %s'%ctrl_id)
            except:
                logger.error('Exception for control:\n%s'%pprint.pformat(ctrl_dict))
                import traceback as tb
                tb.print_exc()
        if len(menu) == 0:
            menu.append(ui.Info_Text("No %s settings found"%menu.label))
        return menu

    def update_control_menu(self):
        from pyglui import ui
        del self.g_pool.capture_source_menu.elements[:]
        del self.uvc_menu[:]
        self.control_id_ui_mapping = {}
        if not self.sensor:
            self.g_pool.capture_source_menu.append(
                ui.Info_Text('Sensor %s @ %s not available. Running in ghost mode.'%(self._sensor_name, self._host_name)))
            return

        uvc_controls = []
        other_controls = []
        for entry in iter(sorted(self.sensor.controls.iteritems())):
            if entry[0].startswith("UVC"):
                uvc_controls.append(entry)
            else: other_controls.append(entry)

        self.add_controls_to_menu(self.g_pool.capture_source_menu, other_controls)
        self.add_controls_to_menu(self.uvc_menu, uvc_controls)
        self.g_pool.capture_source_menu.append(self.uvc_menu)

        self.g_pool.capture_source_menu.append(ui.Button("Reset to default values",self.sensor.reset_all_control_values))

    def cleanup(self):
        if self.sensor:
            self.sensor.unlink()
        self.sensor = None
        self.uvc_menu = None

class NDSI_Manager(Base_Manager):
    """Enumerates and activates Pupil Mobile video sources

    Attributes:
        network (ndsi.Network): NDSI Network backend
        selected_host (unicode): Selected host uuid
    """

    gui_name = 'Pupil Mobile'

    def __init__(self, g_pool):
        super(NDSI_Manager, self).__init__(g_pool)
        self.network = ndsi.Network(callbacks=(self.on_event,))
        self.network.start()
        self.selected_host = None
        self._recover_in = 3
        self._rejoin_in = 400

    def cleanup(self):
        self.deinit_gui()
        self.network.stop()

    def init_gui(self):
        from pyglui import ui
        ui_elements = []
        ui_elements.append(ui.Info_Text('Remote Pupil Mobile sources'))

        def host_selection_list():
            devices = {
                s['host_uuid']: s['host_name'] # removes duplicates
                for s in self.network.sensors.values()
                if s['sensor_type'] == 'video'
            }
            devices = [pair for pair in devices.iteritems()] # create tuples
            # split tuples into 2 lists
            return zip(*(devices or [(None, 'No hosts found')]))

        def view_host(host_uuid):
            if self.selected_host != host_uuid:
                self.selected_host = host_uuid
                self.re_build_ndsi_menu()

        host_sel, host_sel_labels = host_selection_list()
        ui_elements.append(ui.Selector(
            'selected_host',self,
            selection=host_sel,
            labels=host_sel_labels,
            setter=view_host,
            label='Remote host'
        ))

        self.g_pool.capture_selector_menu.extend(ui_elements)
        if not self.selected_host: return
        ui_elements = []

        host_menu = ui.Growing_Menu('Remote Host Information')
        ui_elements.append(host_menu)

        def source_selection_list():
            default = (None, 'Select to activate')
            #self.poll_events()
            sources = [default] + [
                (s['sensor_uuid'], s['sensor_name'])
                for s in self.network.sensors.values()
                if (s['sensor_type'] == 'video' and
                    s['host_uuid'] == self.selected_host)
            ]
            return zip(*sources)

        def activate(source_uid):
            if not source_uid:
                return
            settings = {
                'source_class_name': NDSI_Source.__name__,
                'frame_size': self.g_pool.capture.frame_size,
                'frame_rate': self.g_pool.capture.frame_rate,
                'source_id': source_uid
            }
            self.activate_source(settings)

        src_sel, src_sel_labels = source_selection_list()
        host_menu.append(ui.Selector(
            'selected_source',
            selection=src_sel,
            labels=src_sel_labels,
            getter=lambda: None,
            setter=activate,
            label='Activate source'
        ))

        self.g_pool.capture_selector_menu.extend(ui_elements)

    def re_build_ndsi_menu(self):
        self.deinit_gui()
        self.init_gui()

    def poll_events(self):
        while self.network.has_events:
            self.network.handle_event()

    def recent_events(self,events):
        self.poll_events()

        if (isinstance(self.g_pool.capture, NDSI_Source)
            and not self.g_pool.capture.sensor):
            if self._recover_in <=0:
                self.recover()
                self._recover_in = int(2*1e3/self.g_pool.capture.get_frame_timeout)
            else:
                self._recover_in -= 1

            if self._rejoin_in <=0:
                logger.debug('Rejoining network...')
                self.network.rejoin()
                self._rejoin_in = int(10*1e3/self.g_pool.capture.get_frame_timeout)
            else:
                self._rejoin_in -= 1

    def on_event(self, caller, event):
        if event['subject'] == 'detach':
            logger.debug('detached: %s'%event)
            name = str('%s @ %s'%(event['sensor_name'],event['host_name']))
            self.notify_all({
                'subject': 'capture_manager.source_lost',
                'source_class_name': NDSI_Source.__name__,
                'source_id': event['sensor_uuid'],
                'name': name
            })
            sensors = [s for s in self.network.sensors.values() if s['sensor_type'] == 'video']
            if self.selected_host == event['host_uuid']:
                if sensors:
                    self.selected_host = sensors[0]['host_uuid']
                else:
                    self.selected_host = None
                self.re_build_ndsi_menu()

        elif (event['subject'] == 'attach' and
            event['sensor_type'] == 'video'):
            logger.debug('attached: %s'%event)
            name = '%s @ %s'%(event['sensor_name'],event['host_name'])
            self.notify_all({
                'subject': 'capture_manager.source_found',
                'source_class_name': NDSI_Source.__name__,
                'source_id': event['sensor_uuid'],
                'name': name
            })
            if not self.selected_host:
                self.selected_host = event['host_uuid']
            self.re_build_ndsi_menu()

    def activate_source(self, settings={}):
        settings['network']=self.network
        if hasattr(self.g_pool, 'plugins'):
            self.g_pool.plugins.add(NDSI_Source, args=settings)
        else:
            self.g_pool.replace_source(NDSI_Source.__name__, source_settings=settings)

    def recover(self):
        self.g_pool.capture.recover(self.network)
        print self.g_pool.capture.sensor

    def on_notify(self,n):
        """Provides UI for the capture selection

        Reacts to notification:
            ``capture_manager.source_found``: Check if recovery is possible

        Emmits notifications:
            ``capture_manager.source_found``
            ``capture_manager.source_lost``
        """
        if (n['subject'].startswith('capture_manager.source_found')
            and isinstance(self.g_pool.capture, NDSI_Source)
            and not self.g_pool.capture.sensor):
                self.recover()
