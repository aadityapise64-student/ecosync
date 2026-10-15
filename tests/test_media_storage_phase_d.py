import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
import requests

from services.media_storage import MediaStorageError, persist_file, read_file
from app import create_app
from models import AIScan, User, db


class MediaStoragePhaseDTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SUPABASE_URL='https://project-ref.supabase.co',
            SUPABASE_SECRET_KEY='test-secret-not-a-real-key',
            SUPABASE_STORAGE_BUCKET='private-media',
            SUPABASE_STORAGE_TIMEOUT=7,
            UPLOAD_FOLDER=tempfile.gettempdir(),
        )
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)

    @patch('services.media_storage.requests.post')
    def test_upload_uses_private_server_key_and_generated_object_path(self, post):
        post.return_value = requests.Response()
        post.return_value.status_code = 200
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / 'scan_abc123.jpg'
            source.write_bytes(b'validated image bytes')
            persist_file(str(source), 'scan_abc123.jpg', 'image/jpeg')

        args, kwargs = post.call_args
        self.assertEqual(args[0], 'https://project-ref.supabase.co/storage/v1/object/private-media/media/scan_abc123.jpg')
        self.assertEqual(kwargs['headers']['apikey'], 'test-secret-not-a-real-key')
        self.assertNotIn('Authorization', kwargs['headers'])
        self.assertEqual(kwargs['headers']['Content-Type'], 'image/jpeg')
        self.assertEqual(kwargs['headers']['x-upsert'], 'false')
        self.assertEqual(kwargs['timeout'], 7)

    @patch('services.media_storage.requests.get')
    def test_download_uses_private_bucket_endpoint(self, get):
        response = requests.Response()
        response.status_code = 200
        response._content = b'private-image-data'
        get.return_value = response

        self.assertEqual(read_file('waste_abc123.webp'), b'private-image-data')
        args, kwargs = get.call_args
        self.assertEqual(args[0], 'https://project-ref.supabase.co/storage/v1/object/authenticated/private-media/media/waste_abc123.webp')
        self.assertEqual(kwargs['headers']['apikey'], 'test-secret-not-a-real-key')

    @patch('services.media_storage.requests.post')
    def test_upload_error_does_not_leak_provider_body_or_key(self, post):
        response = requests.Response()
        response.status_code = 403
        response._content = b'test-secret-not-a-real-key provider error body'
        post.return_value = response
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / 'scan_abc123.jpg'
            source.write_bytes(b'image')
            with self.assertRaisesRegex(MediaStorageError, 'HTTP 403') as raised:
                persist_file(str(source), 'scan_abc123.jpg', 'image/jpeg')
        self.assertNotIn('test-secret-not-a-real-key', str(raised.exception))
        self.assertNotIn('provider error body', str(raised.exception))

    @patch('services.media_storage.requests.post')
    def test_local_mode_does_not_call_supabase(self, post):
        self.app.config['SUPABASE_SECRET_KEY'] = ''
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / 'scan_abc123.jpg'
            source.write_bytes(b'image')
            persist_file(str(source), 'scan_abc123.jpg', 'image/jpeg')
        post.assert_not_called()


class MediaRouteAuthorizationTests(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = 'phase-d-test-only'
        WTF_CSRF_ENABLED = False
        SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        SQLALCHEMY_ENGINE_OPTIONS = {'pool_pre_ping': True}
        UPLOAD_FOLDER = tempfile.gettempdir()
        MAX_CONTENT_LENGTH = 16 * 1024 * 1024
        MAX_IMAGE_PIXELS = 20_000_000
        ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
        SUPABASE_URL = 'https://project-ref.supabase.co'
        SUPABASE_SECRET_KEY = 'test-secret-not-a-real-key'
        SUPABASE_STORAGE_BUCKET = 'private-media'

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.owner = User(name='Owner', email='owner@example.test', role='household')
        self.other = User(name='Other', email='other@example.test', role='household')
        self.owner.set_password('unused-test-password')
        self.other.set_password('unused-test-password')
        db.session.add_all([self.owner, self.other])
        db.session.flush()
        db.session.add(AIScan(
            user_id=self.owner.id,
            original_filename='scan_private1.jpg',
            status='live',
            predictions_json='[]',
            class_counts_json='{}',
        ))
        db.session.commit()
        self.client = self.app.test_client()
        self.addCleanup(self.teardown_app)

    def teardown_app(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def login_as(self, client, user_id):
        with client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

    def test_partial_supabase_configuration_fails_closed(self):
        with patch.object(self.TestConfig, 'SUPABASE_SECRET_KEY', ''):
            with self.assertRaisesRegex(RuntimeError, 'Configure both SUPABASE_URL and SUPABASE_SECRET_KEY'):
                create_app(self.TestConfig)

    def test_deployment_can_require_cloud_storage(self):
        with patch.object(self.TestConfig, 'SUPABASE_URL', ''), \
             patch.object(self.TestConfig, 'SUPABASE_SECRET_KEY', ''), \
             patch.object(self.TestConfig, 'REQUIRE_CLOUD_STORAGE', True, create=True):
            with self.assertRaisesRegex(RuntimeError, 'Supabase private storage is required'):
                create_app(self.TestConfig)

    @patch('app.read_file')
    def test_owner_can_proxy_private_media_with_no_store_headers(self, read_file_mock):
        read_file_mock.return_value = b'private test image'
        self.login_as(self.client, self.owner.id)

        response = self.client.get('/media/scan_private1.jpg')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b'private test image')
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        read_file_mock.assert_called_once_with('scan_private1.jpg')

    @patch('app.read_file')
    def test_other_household_cannot_request_private_object(self, read_file_mock):
        self.login_as(self.client, self.other.id)

        response = self.client.get('/media/scan_private1.jpg')

        self.assertEqual(response.status_code, 404)
        read_file_mock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
