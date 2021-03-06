# -*- coding: utf-8 -*-
import datetime
import json
import mock
from django.test import TestCase
from django.core import mail
from django.test.utils import override_settings


def create_dummy_error_data(**kwargs):
    """ return dummy error data
    """
    data = {
        'page': 'http://localhost/?test=key',
        'url': 'http://localhost/static/app.js',
        'message': 'Uncaught ReferenceError: aaa is not defined',
        'line': 87,
        'when': 'before',
        'user_agent': ('Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.4 '
                       '(KHTML, like Gecko) Chrome/22.0.1229.92 Safari/537.4'),
    }
    data.update(kwargs)
    return data


def create_post_data(errors, prefix='form-'):
    """ return post data
    """
    from django.forms.formsets import TOTAL_FORM_COUNT, INITIAL_FORM_COUNT
    name = prefix + '%s-%s'
    post_data = {}
    for cnt, err in enumerate(errors):
        post_data.update(dict((name % (cnt, k), v) for k, v in err.items()))
    post_data.update({
        prefix + INITIAL_FORM_COUNT: 0,
        prefix + TOTAL_FORM_COUNT: len(errors)
    })
    return post_data


def get_log_view_url():
    """ return url of log view
    """
    from django.core.urlresolvers import reverse
    return reverse('add_log')


class LogViewTests(TestCase):
    urls = 'jserrorlogging.urls'

    def setUp(self):
        self.user = self._create_user(self)

    def _create_user(self, username='test_user'):
        from django.contrib.auth.models import User
        return User.objects.create_user('test_user', password='test')

    def _login(self, user):
        self.assertTrue(
            self.client.login(username=self.user.username, password='test'))

    def _assert_response(self, response, count=1):
        self.assertEqual(response.status_code, 200)
        self.assertTrue('Posted %s errors' % count in response.content,
                        response.content)

    def _assert_latest_log(self, **data):
        from .models import Log
        log = Log.objects.latest('id')
        for k, v in data.items():
            val = getattr(log, k)
            self.assertEqual(
                val, v, u'%s != %s (%s)' % (val, v, k))

    def test_it(self):
        data = [create_dummy_error_data()]
        res = self.client.post(get_log_view_url(), create_post_data(data))
        self._assert_response(res)
        valid_data = {
            'meta': '',
            'browser': 'Chrome',
            'user_id': None,
            'session_key': '',
            'remote_addr': '127.0.0.1'
        }
        valid_data.update(data[0])
        self._assert_latest_log(**valid_data)

    def test_it_multiple(self):
        data = [create_dummy_error_data()]
        data.append(create_dummy_error_data())
        res = self.client.post(get_log_view_url(), create_post_data(data))
        self._assert_response(res, count=2)

    def test_it_with_user(self):
        """ post by logged in user
        """
        data = [create_dummy_error_data()]
        self._login(self.user)
        res = self.client.post(get_log_view_url(), create_post_data(data))
        self._assert_response(res)
        self._assert_latest_log(user_id=self.user.id)

    def test_it_with_session(self):
        """ user has session
        """
        data = [create_dummy_error_data()]
        # set session data
        from django.conf import settings
        from django.utils.importlib import import_module
        engine = import_module(settings.SESSION_ENGINE)
        store = engine.SessionStore()
        store.save()
        session_key = store.session_key
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session_key
        self.client.post(get_log_view_url(), create_post_data(data))
        self._assert_latest_log(session_key=session_key)

    def test_it_with_meta(self):
        data = [create_dummy_error_data()]
        meta_data = [{'name': 'meta', 'value': 'dummy'}]
        post_data = create_post_data(data)
        post_data.update(create_post_data(meta_data, prefix='form0-'))
        res = self.client.post(get_log_view_url(), post_data)
        self._assert_response(res)
        self._assert_latest_log(meta=json.dumps(dict([(v['name'], v['value']) for v in meta_data])))

    def test_not_allowed_method(self):
        res = self.client.get(get_log_view_url())
        self.assertEqual(res.status_code, 405)

    def test_signal_save_model(self):
        data = [create_dummy_error_data()]
        self.client.post(get_log_view_url(), create_post_data(data))
        from .models import Log
        cnt = Log.objects.filter(message=data[0]['message']).count()
        self.assertEqual(1, cnt)

    @override_settings(
        JSERRORLOGGING_MAIL_TO=[('Admin', 'admin@example.com')])
    def test_signal_notify_by_email(self):
        # same error message is cached and don't send
        data = [create_dummy_error_data(message='SyntaxError -dummy for test-',
                                        line=0)]
        self.client.post(get_log_view_url(), create_post_data(data))
        # check notify
        from django.conf import settings
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            u'%sJS_ERROR: %s' % (settings.EMAIL_SUBJECT_PREFIX, data[0]['message']))
        self.assertTrue(data[0]['message'] in mail.outbox[0].body, mail.outbox[0].body)


class ReceiverNotifyByEmailTests(TestCase):
    """ notify by email
    """

    def setUp(self):
        # clear cache for notify interval
        from django.core.cache import cache
        cache.clear()

    def _get_it(self):
        from .receivers import notify_by_email
        return notify_by_email

    @mock.patch('django.utils.timezone.now')
    def test_it_body(self, dummy_now):
        dummy_now.return_value = datetime.datetime(2012, 12, 17, 10, 20)
        data = create_dummy_error_data()
        data.update(
            user_id=1, session_key='dummy_session_key',
            remote_addr='127.0.0.1',
            created_at=dummy_now.return_value)
        self._get_it()('sender', data=data)
        from django.core import mail
        data.update(created_at=data['created_at'].strftime('%Y-%m-%d %H:%M:%S'))
        body = (
            u'## %(message)s\n'
            u'\n'
            u'Where:      %(line)s in %(url)s\n'
            u'UserAgent:  %(user_agent)s\n'
            u'When:       Before page load\n'
            u'On Page:    %(page)s\n'
            u'Date:       %(created_at)s\n'
            u'RemoteAddr: %(remote_addr)s\n'
            u'UserID:     %(user_id)s\n'
            u'SessionKey: %(session_key)s\n\n'
        ) % data
        self.assertEqual(mail.outbox[0].body, body)
