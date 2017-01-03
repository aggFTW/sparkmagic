from mock import MagicMock, patch
from nose.tools import with_setup, raises, assert_equals, assert_is
from tornado.concurrent import Future
from tornado.web import MissingArgumentError
from tornado.testing import gen_test
from tornado.testing import AsyncTestCase
import json

from sparkmagic.serverextension.handlers import ReconnectHandler
from sparkmagic.kernels.kernelmagics import KernelMagics
import sparkmagic.utils.configuration as conf


class SimpleObject(object):
    pass


class TestSparkMagicHandler(AsyncTestCase):
    reconnect_handler = None
    session_manager = None
    kernel_manager = None
    individual_kernel_manager = None
    client = None
    session_list = None
    spark_events = None
    path = 'some_path.ipynb'
    kernel_id = '1'
    kernel_name = 'pysparkkernel'
    session_id = '1'
    username = 'username'
    password = 'password'
    endpoint = 'http://endpoint.com'
    response_id = '0'
    good_msg = dict(content=dict(status='ok'))
    bad_msg = dict(content=dict(status='error', ename='SyntaxError', evalue='oh no!'))
    request = None

    def create_session_dict(self, path, kernel_id):
        return dict(notebook=dict(path=path), kernel=dict(id=kernel_id, name=self.kernel_name), id=self.session_id)

    def get_argument(self, key):
        return dict(username=self.username, password=self.password, endpoint=self.endpoint, path=self.path)[key]

    def setUp(self):
        # Mock kernel manager
        self.client = MagicMock()
        self.client.execute = MagicMock(return_value=self.response_id)
        self.client.get_shell_msg = MagicMock(return_value=self.good_msg)
        self.individual_kernel_manager = MagicMock()
        self.individual_kernel_manager.client = MagicMock(return_value=self.client)
        self.kernel_manager = MagicMock()
        self.kernel_manager.get_kernel = MagicMock(return_value=self.individual_kernel_manager)

        # Mock session manager
        self.session_list = [self.create_session_dict(self.path, self.kernel_id)]
        self.session_manager = MagicMock()
        self.session_manager.list_sessions = MagicMock(return_value=self.session_list)
        self.session_manager.create_session = MagicMock(return_value=self.create_session_dict(self.path, self.kernel_id))

        # Mock spark events
        self.spark_events = MagicMock()

        # Mock request
        self.request = MagicMock()
        self.request.body = json.dumps({"path": self.path, "username": self.username, "password": self.password, "endpoint": self.endpoint})

        # Create mocked reconnect_handler
        ReconnectHandler.__bases__ = (SimpleObject,)
        self.reconnect_handler = ReconnectHandler()
        self.reconnect_handler.spark_events = self.spark_events
        self.reconnect_handler.session_manager = self.session_manager
        self.reconnect_handler.kernel_manager = self.kernel_manager
        self.reconnect_handler.set_status = MagicMock()
        self.reconnect_handler.finish = MagicMock()
        self.reconnect_handler.current_user = 'alex'
        self.reconnect_handler.request = self.request

        super(TestSparkMagicHandler, self).setUp()

    def test_msg_status(self):
        assert_equals(self.reconnect_handler._msg_status(self.good_msg), 'ok')
        assert_equals(self.reconnect_handler._msg_status(self.bad_msg), 'error')

    def test_msg_successful(self):
        assert_equals(self.reconnect_handler._msg_successful(self.good_msg), True)
        assert_equals(self.reconnect_handler._msg_successful(self.bad_msg), False)

    def test_msg_error(self):
        assert_equals(self.reconnect_handler._msg_error(self.good_msg), None)
        assert_equals(self.reconnect_handler._msg_error(self.bad_msg), u'{}:\n{}'.format('SyntaxError', 'oh no!'))

    @gen_test
    def test_post_no_json(self):
        self.reconnect_handler.request.body = "{{}"

        res = yield self.reconnect_handler.post()
        assert_equals(res, None)

        msg = "Invalid JSON in request body."
        self.reconnect_handler.set_status.assert_called_once_with(400)
        self.reconnect_handler.finish.assert_called_once_with(msg)
        self.spark_events.emit_cluster_change_event.assert_called_once_with(None, 400, False, msg)

    @gen_test
    def test_post_no_key(self):
        self.reconnect_handler.request.body = json.dumps({})

        res = yield self.reconnect_handler.post()
        assert_equals(res, None)

        msg = 'HTTP 400: Bad Request (Missing argument path)'
        self.reconnect_handler.set_status.assert_called_once_with(400)
        self.reconnect_handler.finish.assert_called_once_with(msg)
        self.spark_events.emit_cluster_change_event.assert_called_once_with(None, 400, False, msg)

    @patch('sparkmagic.serverextension.handlers.ReconnectHandler._get_kernel_manager')
    @gen_test
    def test_post_existing_kernel(self, _get_kernel_manager):
        kernel_manager_future = Future()
        kernel_manager_future.set_result(self.individual_kernel_manager)
        _get_kernel_manager.return_value = kernel_manager_future

        res = yield self.reconnect_handler.post()
        assert_equals(res, None)

        code = '%{} -s {} -u {} -p {}'.format(KernelMagics._do_not_call_change_endpoint.__name__, self.endpoint, self.username, self.password)
        self.client.execute.assert_called_once_with(code, silent=False, store_history=False)
        self.reconnect_handler.set_status.assert_called_once_with(200)
        self.reconnect_handler.finish.assert_called_once_with('{"error": null, "success": true}')
        self.spark_events.emit_cluster_change_event.assert_called_once_with(self.endpoint, 200, True, None)

    @patch('sparkmagic.serverextension.handlers.ReconnectHandler._get_kernel_manager')
    @gen_test
    def test_post_existing_kernel_failed(self, _get_kernel_manager):
        kernel_manager_future = Future()
        kernel_manager_future.set_result(self.individual_kernel_manager)
        _get_kernel_manager.return_value = kernel_manager_future
        self.client.get_shell_msg = MagicMock(return_value=self.bad_msg)

        res = yield self.reconnect_handler.post()
        assert_equals(res, None)

        code = '%{} -s {} -u {} -p {}'.format(KernelMagics._do_not_call_change_endpoint.__name__, self.endpoint, self.username, self.password)
        self.client.execute.assert_called_once_with(code, silent=False, store_history=False)
        self.reconnect_handler.set_status.assert_called_once_with(500)
        self.reconnect_handler.finish.assert_called_once_with('{"error": "SyntaxError:\\noh no!", "success": false}')
        self.spark_events.emit_cluster_change_event.assert_called_once_with(self.endpoint, 500, False, "SyntaxError:\noh no!")

    # @patch('sparkmagic.serverextension.handlers.ReconnectHandler._get_kernel_manager_new_session')
    # @gen_test
    # def test_test_get_kernel_manager_no_existing_kernel1(self, _get_kernel_manager_new_session):
    #     future_1 = Future()
    #     kernel_manager = MagicMock()
    #     future_1.set_result(kernel_manager)
    #     _get_kernel_manager_new_session.return_value = future_1
    #     result = yield self.reconnect_handler._get_kernel_manager_new_session("a", "b")
    #     assert_equals(result, kernel_manager)

# @with_setup(_setup, _teardown)
# def test_get_kernel_manager_no_existing_kernel():
#     kernel_name = "kernel"
#     reconnect_handler._get_kernel_manager('not_existing_path.ipynb', kernel_name)
    
#     session_manager.create_session.assert_called_once_with(kernel_name=kernel_name, path=path)
#     kernel_manager.get_kernel.assert_called_once_with(kernel_id)
