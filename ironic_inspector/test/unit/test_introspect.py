# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import collections
import time

import eventlet
from ironicclient import exceptions
import mock
from oslo_config import cfg

from ironic_inspector.common import ironic as ir_utils
from ironic_inspector import firewall
from ironic_inspector import introspect
from ironic_inspector import node_cache
from ironic_inspector.test import base as test_base
from ironic_inspector import utils

CONF = cfg.CONF


class BaseTest(test_base.NodeTest):
    def setUp(self):
        super(BaseTest, self).setUp()
        introspect._LAST_INTROSPECTION_TIME = 0
        self.node.power_state = 'power off'
        self.ports = [mock.Mock(address=m) for m in self.macs]
        self.ports_dict = collections.OrderedDict((p.address, p)
                                                  for p in self.ports)
        self.node_info = mock.Mock(uuid=self.uuid, options={})
        self.node_info.ports.return_value = self.ports_dict
        self.node_info.node.return_value = self.node

    def _prepare(self, client_mock):
        cli = client_mock.return_value
        cli.node.get.return_value = self.node
        cli.node.validate.return_value = mock.Mock(power={'result': True})
        return cli


@mock.patch.object(eventlet.greenthread, 'sleep', lambda _: None)
@mock.patch.object(firewall, 'update_filters', autospec=True)
@mock.patch.object(node_cache, 'start_introspection', autospec=True)
@mock.patch.object(ir_utils, 'get_client', autospec=True)
class TestIntrospect(BaseTest):
    def test_ok(self, client_mock, start_mock, filters_mock):
        cli = self._prepare(client_mock)
        start_mock.return_value = self.node_info

        introspect.introspect(self.node.uuid)

        cli.node.get.assert_called_once_with(self.uuid)
        cli.node.validate.assert_called_once_with(self.uuid)

        start_mock.assert_called_once_with(self.uuid,
                                           bmc_address=self.bmc_address,
                                           ironic=cli)
        self.node_info.ports.assert_called_once_with()
        self.node_info.add_attribute.assert_called_once_with('mac',
                                                             self.macs)
        filters_mock.assert_called_with(cli)
        cli.node.set_boot_device.assert_called_once_with(self.uuid,
                                                         'pxe',
                                                         persistent=False)
        cli.node.set_power_state.assert_called_once_with(self.uuid,
                                                         'reboot')
        self.node_info.set_option.assert_called_once_with(
            'new_ipmi_credentials', None)
        self.node_info.acquire_lock.assert_called_once_with()
        self.node_info.release_lock.assert_called_once_with()

    def test_loopback_bmc_address(self, client_mock, start_mock, filters_mock):
        self.node.driver_info['ipmi_address'] = '127.0.0.1'
        cli = self._prepare(client_mock)
        start_mock.return_value = self.node_info

        introspect.introspect(self.node.uuid)

        cli.node.get.assert_called_once_with(self.uuid)
        cli.node.validate.assert_called_once_with(self.uuid)

        start_mock.assert_called_once_with(self.uuid,
                                           bmc_address=None,
                                           ironic=cli)
        self.node_info.ports.assert_called_once_with()
        self.node_info.add_attribute.assert_called_once_with('mac',
                                                             self.macs)
        filters_mock.assert_called_with(cli)
        cli.node.set_boot_device.assert_called_once_with(self.uuid,
                                                         'pxe',
                                                         persistent=False)
        cli.node.set_power_state.assert_called_once_with(self.uuid,
                                                         'reboot')
        self.node_info.set_option.assert_called_once_with(
            'new_ipmi_credentials', None)
        self.node_info.acquire_lock.assert_called_once_with()
        self.node_info.release_lock.assert_called_once_with()

    def test_ok_ilo_and_drac(self, client_mock, start_mock, filters_mock):
        cli = self._prepare(client_mock)
        start_mock.return_value = self.node_info

        for name in ('ilo_address', 'drac_host'):
            self.node.driver_info = {name: self.bmc_address}
            introspect.introspect(self.node.uuid)

        start_mock.assert_called_with(self.uuid,
                                      bmc_address=self.bmc_address,
                                      ironic=cli)

    def test_power_failure(self, client_mock, start_mock, filters_mock):
        cli = self._prepare(client_mock)
        cli.node.set_boot_device.side_effect = exceptions.BadRequest()
        cli.node.set_power_state.side_effect = exceptions.BadRequest()
        start_mock.return_value = self.node_info

        introspect.introspect(self.node.uuid)

        cli.node.get.assert_called_once_with(self.uuid)

        start_mock.assert_called_once_with(self.uuid,
                                           bmc_address=self.bmc_address,
                                           ironic=cli)
        cli.node.set_boot_device.assert_called_once_with(self.uuid,
                                                         'pxe',
                                                         persistent=False)
        cli.node.set_power_state.assert_called_once_with(self.uuid,
                                                         'reboot')
        start_mock.return_value.finished.assert_called_once_with(
            error=mock.ANY)
        self.node_info.acquire_lock.assert_called_once_with()
        self.node_info.release_lock.assert_called_once_with()

    def test_unexpected_error(self, client_mock, start_mock, filters_mock):
        cli = self._prepare(client_mock)
        start_mock.return_value = self.node_info
        filters_mock.side_effect = RuntimeError()

        introspect.introspect(self.node.uuid)

        cli.node.get.assert_called_once_with(self.uuid)

        start_mock.assert_called_once_with(self.uuid,
                                           bmc_address=self.bmc_address,
                                           ironic=cli)
        self.assertFalse(cli.node.set_boot_device.called)
        start_mock.return_value.finished.assert_called_once_with(
            error=mock.ANY)
        self.node_info.acquire_lock.assert_called_once_with()
        self.node_info.release_lock.assert_called_once_with()

    def test_no_macs(self, client_mock, start_mock, filters_mock):
        cli = self._prepare(client_mock)
        self.node_info.ports.return_value = []
        start_mock.return_value = self.node_info

        introspect.introspect(self.node.uuid)

        self.node_info.ports.assert_called_once_with()

        start_mock.assert_called_once_with(self.uuid,
                                           bmc_address=self.bmc_address,
                                           ironic=cli)
        self.assertFalse(self.node_info.add_attribute.called)
        self.assertFalse(filters_mock.called)
        cli.node.set_boot_device.assert_called_once_with(self.uuid,
                                                         'pxe',
                                                         persistent=False)
        cli.node.set_power_state.assert_called_once_with(self.uuid,
                                                         'reboot')

    def test_no_lookup_attrs(self, client_mock, start_mock, filters_mock):
        cli = self._prepare(client_mock)
        self.node_info.ports.return_value = []
        start_mock.return_value = self.node_info
        self.node_info.attributes = {}

        introspect.introspect(self.uuid)

        self.node_info.ports.assert_called_once_with()
        self.node_info.finished.assert_called_once_with(error=mock.ANY)
        self.assertEqual(0, filters_mock.call_count)
        self.assertEqual(0, cli.node.set_power_state.call_count)
        self.node_info.acquire_lock.assert_called_once_with()
        self.node_info.release_lock.assert_called_once_with()

    def test_no_lookup_attrs_with_node_not_found_hook(self, client_mock,
                                                      start_mock,
                                                      filters_mock):
        CONF.set_override('node_not_found_hook', 'example', 'processing')
        cli = self._prepare(client_mock)
        self.node_info.ports.return_value = []
        start_mock.return_value = self.node_info
        self.node_info.attributes = {}

        introspect.introspect(self.uuid)

        self.node_info.ports.assert_called_once_with()
        self.assertFalse(self.node_info.finished.called)
        cli.node.set_boot_device.assert_called_once_with(self.uuid,
                                                         'pxe',
                                                         persistent=False)
        cli.node.set_power_state.assert_called_once_with(self.uuid,
                                                         'reboot')

    def test_failed_to_get_node(self, client_mock, start_mock, filters_mock):
        cli = client_mock.return_value
        cli.node.get.side_effect = exceptions.NotFound()
        self.assertRaisesRegex(utils.Error,
                               'Node %s was not found' % self.uuid,
                               introspect.introspect, self.uuid)

        cli.node.get.side_effect = exceptions.BadRequest()
        self.assertRaisesRegex(utils.Error,
                               '%s: Bad Request' % self.uuid,
                               introspect.introspect, self.uuid)

        self.assertEqual(0, self.node_info.ports.call_count)
        self.assertEqual(0, filters_mock.call_count)
        self.assertEqual(0, cli.node.set_power_state.call_count)
        self.assertFalse(start_mock.called)
        self.assertFalse(self.node_info.acquire_lock.called)

    def test_failed_to_validate_node(self, client_mock, start_mock,
                                     filters_mock):
        cli = client_mock.return_value
        cli.node.get.return_value = self.node
        cli.node.validate.return_value = mock.Mock(power={'result': False,
                                                          'reason': 'oops'})

        self.assertRaisesRegex(
            utils.Error,
            'Failed validation of power interface',
            introspect.introspect, self.uuid)

        cli.node.validate.assert_called_once_with(self.uuid)
        self.assertEqual(0, self.node_info.ports.call_count)
        self.assertEqual(0, filters_mock.call_count)
        self.assertEqual(0, cli.node.set_power_state.call_count)
        self.assertFalse(start_mock.called)
        self.assertFalse(self.node_info.acquire_lock.called)

    def test_wrong_provision_state(self, client_mock, start_mock,
                                   filters_mock):
        self.node.provision_state = 'active'
        cli = client_mock.return_value
        cli.node.get.return_value = self.node

        self.assertRaisesRegex(
            utils.Error, 'Invalid provision state for introspection: "active"',
            introspect.introspect, self.uuid)

        self.assertEqual(0, self.node_info.ports.call_count)
        self.assertEqual(0, filters_mock.call_count)
        self.assertEqual(0, cli.node.set_power_state.call_count)
        self.assertFalse(start_mock.called)
        self.assertFalse(self.node_info.acquire_lock.called)

    @mock.patch.object(time, 'sleep')
    @mock.patch.object(time, 'time')
    def test_introspection_delay(self, time_mock, sleep_mock, client_mock,
                                 start_mock, filters_mock):
        time_mock.return_value = 42
        introspect._LAST_INTROSPECTION_TIME = 40
        CONF.set_override('introspection_delay', 10)

        cli = self._prepare(client_mock)
        start_mock.return_value = self.node_info

        introspect.introspect(self.uuid)

        sleep_mock.assert_called_once_with(8)
        cli.node.set_boot_device.assert_called_once_with(self.uuid,
                                                         'pxe',
                                                         persistent=False)
        cli.node.set_power_state.assert_called_once_with(self.uuid,
                                                         'reboot')
        # updated to the current time.time()
        self.assertEqual(42, introspect._LAST_INTROSPECTION_TIME)

    @mock.patch.object(time, 'sleep')
    @mock.patch.object(time, 'time')
    def test_introspection_delay_not_needed(self, time_mock, sleep_mock,
                                            client_mock, start_mock,
                                            filters_mock):
        time_mock.return_value = 100
        introspect._LAST_INTROSPECTION_TIME = 40
        CONF.set_override('introspection_delay', 10)

        cli = self._prepare(client_mock)
        start_mock.return_value = self.node_info

        introspect.introspect(self.uuid)

        self.assertFalse(sleep_mock.called)
        cli.node.set_boot_device.assert_called_once_with(self.uuid,
                                                         'pxe',
                                                         persistent=False)
        cli.node.set_power_state.assert_called_once_with(self.uuid,
                                                         'reboot')
        # updated to the current time.time()
        self.assertEqual(100, introspect._LAST_INTROSPECTION_TIME)

    @mock.patch.object(time, 'sleep')
    @mock.patch.object(time, 'time')
    def test_introspection_delay_custom_drivers(self, time_mock, sleep_mock,
                                                client_mock, start_mock,
                                                filters_mock):
        self.node.driver = 'foobar'
        time_mock.return_value = 42
        introspect._LAST_INTROSPECTION_TIME = 40
        CONF.set_override('introspection_delay', 10)
        CONF.set_override('introspection_delay_drivers', 'fo{1,2}b.r')

        cli = self._prepare(client_mock)
        start_mock.return_value = self.node_info

        introspect.introspect(self.uuid)

        sleep_mock.assert_called_once_with(8)
        cli.node.set_boot_device.assert_called_once_with(self.uuid,
                                                         'pxe',
                                                         persistent=False)
        cli.node.set_power_state.assert_called_once_with(self.uuid,
                                                         'reboot')
        # updated to the current time.time()
        self.assertEqual(42, introspect._LAST_INTROSPECTION_TIME)


@mock.patch.object(firewall, 'update_filters', autospec=True)
@mock.patch.object(node_cache, 'start_introspection', autospec=True)
@mock.patch.object(ir_utils, 'get_client', autospec=True)
class TestSetIpmiCredentials(BaseTest):
    def setUp(self):
        super(TestSetIpmiCredentials, self).setUp()
        CONF.set_override('enable_setting_ipmi_credentials', True,
                          'processing')
        self.new_creds = ('user', 'password')
        self.node_info.options['new_ipmi_credentials'] = self.new_creds
        self.node.provision_state = 'enroll'

    def test_ok(self, client_mock, start_mock, filters_mock):
        cli = self._prepare(client_mock)
        start_mock.return_value = self.node_info

        introspect.introspect(self.uuid, new_ipmi_credentials=self.new_creds)

        start_mock.assert_called_once_with(self.uuid,
                                           bmc_address=self.bmc_address,
                                           ironic=cli)
        filters_mock.assert_called_with(cli)
        self.assertFalse(cli.node.validate.called)
        self.assertFalse(cli.node.set_boot_device.called)
        self.assertFalse(cli.node.set_power_state.called)
        start_mock.return_value.set_option.assert_called_once_with(
            'new_ipmi_credentials', self.new_creds)

    def test_disabled(self, client_mock, start_mock, filters_mock):
        CONF.set_override('enable_setting_ipmi_credentials', False,
                          'processing')
        self._prepare(client_mock)

        self.assertRaisesRegex(utils.Error, 'disabled',
                               introspect.introspect, self.uuid,
                               new_ipmi_credentials=self.new_creds)

    def test_no_username(self, client_mock, start_mock, filters_mock):
        self._prepare(client_mock)

        self.assertRaises(utils.Error, introspect.introspect, self.uuid,
                          new_ipmi_credentials=(None, 'password'))

    def test_default_username(self, client_mock, start_mock, filters_mock):
        cli = self._prepare(client_mock)
        start_mock.return_value = self.node_info
        self.node.driver_info['ipmi_username'] = self.new_creds[0]

        introspect.introspect(self.uuid,
                              new_ipmi_credentials=(None, self.new_creds[1]))

        start_mock.assert_called_once_with(self.uuid,
                                           bmc_address=self.bmc_address,
                                           ironic=cli)
        filters_mock.assert_called_with(cli)
        self.assertFalse(cli.node.validate.called)
        self.assertFalse(cli.node.set_boot_device.called)
        self.assertFalse(cli.node.set_power_state.called)
        start_mock.return_value.set_option.assert_called_once_with(
            'new_ipmi_credentials', self.new_creds)

    def test_wrong_letters(self, client_mock, start_mock, filters_mock):
        self.new_creds = ('user', 'p ssw@rd')
        self._prepare(client_mock)

        self.assertRaises(utils.Error, introspect.introspect, self.uuid,
                          new_ipmi_credentials=self.new_creds)

    def test_too_long(self, client_mock, start_mock, filters_mock):
        self.new_creds = ('user', 'password' * 100)
        self._prepare(client_mock)

        self.assertRaises(utils.Error, introspect.introspect, self.uuid,
                          new_ipmi_credentials=self.new_creds)

    def test_wrong_state(self, client_mock, start_mock, filters_mock):
        self.node.provision_state = 'manageable'
        self._prepare(client_mock)

        self.assertRaises(utils.Error, introspect.introspect, self.uuid,
                          new_ipmi_credentials=self.new_creds)


@mock.patch.object(firewall, 'update_filters', autospec=True)
@mock.patch.object(node_cache, 'get_node', autospec=True)
@mock.patch.object(ir_utils, 'get_client', autospec=True)
class TestAbort(BaseTest):
    def setUp(self):
        super(TestAbort, self).setUp()
        self.node_info.started_at = None
        self.node_info.finished_at = None

    def test_ok(self, client_mock, get_mock, filters_mock):
        cli = self._prepare(client_mock)
        get_mock.return_value = self.node_info
        self.node_info.acquire_lock.return_value = True
        self.node_info.started_at = time.time()
        self.node_info.finished_at = None

        introspect.abort(self.node.uuid)

        get_mock.assert_called_once_with(self.uuid, ironic=cli,
                                         locked=False)
        self.node_info.acquire_lock.assert_called_once_with(blocking=False)
        filters_mock.assert_called_once_with(cli)
        cli.node.set_power_state.assert_called_once_with(self.uuid, 'off')
        self.node_info.finished.assert_called_once_with(error='Canceled '
                                                        'by operator')

    def test_node_not_found(self, client_mock, get_mock, filters_mock):
        cli = self._prepare(client_mock)
        exc = utils.Error('Not found.', code=404)
        get_mock.side_effect = exc

        self.assertRaisesRegex(utils.Error, str(exc),
                               introspect.abort, self.uuid)

        self.assertEqual(0, filters_mock.call_count)
        self.assertEqual(0, cli.node.set_power_state.call_count)
        self.assertEqual(0, self.node_info.finished.call_count)

    def test_node_locked(self, client_mock, get_mock, filters_mock):
        cli = self._prepare(client_mock)
        get_mock.return_value = self.node_info
        self.node_info.acquire_lock.return_value = False
        self.node_info.started_at = time.time()

        self.assertRaisesRegex(utils.Error, 'Node is locked, please, '
                               'retry later', introspect.abort, self.uuid)

        self.assertEqual(0, filters_mock.call_count)
        self.assertEqual(0, cli.node.set_power_state.call_count)
        self.assertEqual(0, self.node_info.finshed.call_count)

    def test_introspection_already_finished(self, client_mock,
                                            get_mock, filters_mock):
        cli = self._prepare(client_mock)
        get_mock.return_value = self.node_info
        self.node_info.acquire_lock.return_value = True
        self.node_info.started_at = time.time()
        self.node_info.finished_at = time.time()

        introspect.abort(self.uuid)

        self.assertEqual(0, filters_mock.call_count)
        self.assertEqual(0, cli.node.set_power_state.call_count)
        self.assertEqual(0, self.node_info.finshed.call_count)

    def test_firewall_update_exception(self, client_mock, get_mock,
                                       filters_mock):
        cli = self._prepare(client_mock)
        get_mock.return_value = self.node_info
        self.node_info.acquire_lock.return_value = True
        self.node_info.started_at = time.time()
        self.node_info.finished_at = None
        filters_mock.side_effect = Exception('Boom')

        introspect.abort(self.uuid)

        get_mock.assert_called_once_with(self.uuid, ironic=cli,
                                         locked=False)
        self.node_info.acquire_lock.assert_called_once_with(blocking=False)
        filters_mock.assert_called_once_with(cli)
        cli.node.set_power_state.assert_called_once_with(self.uuid, 'off')
        self.node_info.finished.assert_called_once_with(error='Canceled '
                                                        'by operator')

    def test_node_power_off_exception(self, client_mock, get_mock,
                                      filters_mock):
        cli = self._prepare(client_mock)
        get_mock.return_value = self.node_info
        self.node_info.acquire_lock.return_value = True
        self.node_info.started_at = time.time()
        self.node_info.finished_at = None
        cli.node.set_power_state.side_effect = Exception('BadaBoom')

        introspect.abort(self.uuid)

        get_mock.assert_called_once_with(self.uuid, ironic=cli,
                                         locked=False)
        self.node_info.acquire_lock.assert_called_once_with(blocking=False)
        filters_mock.assert_called_once_with(cli)
        cli.node.set_power_state.assert_called_once_with(self.uuid, 'off')
        self.node_info.finished.assert_called_once_with(error='Canceled '
                                                        'by operator')
