from django.test import Client, TestCase, override_settings

from workspaces.models import Workspace
from workspaces.services.workspace_files import (
    content_type_for_file_path,
    rewrite_workspace_asset_refs,
    validate_workspace_file_path,
)
from workspaces.storage import get_storage, reset_storage
from workspaces.tests import IsolatedStorageTestCase


class WorkspaceFileUrlTests(IsolatedStorageTestCase):
    def test_file_url_is_root_relative_by_default(self):
        reset_storage()
        storage = get_storage()
        ws_id = "abc-123"
        url = storage.file_url(ws_id, "lessons/0001.html")
        self.assertEqual(url, "/workspaces/abc-123/lessons/0001.html")

    @override_settings(WORKSPACES_PUBLIC_BASE_URL="http://localhost:8000")
    def test_file_url_can_be_absolute_when_configured(self):
        reset_storage()
        storage = get_storage()
        ws_id = "abc-123"
        url = storage.file_url(ws_id, "lessons/0001.html")
        self.assertEqual(
            url, "http://localhost:8000/workspaces/abc-123/lessons/0001.html"
        )


class RewriteAssetRefsTests(TestCase):
    def test_rewrites_relative_asset_paths_to_workspace_root(self):
        html = (
            '<link rel="stylesheet" href="../assets/lesson.css">'
            '<script src="../assets/quiz.js"></script>'
        )
        out = rewrite_workspace_asset_refs(html, "ws-1")
        self.assertIn('href="/workspaces/ws-1/assets/lesson.css"', out)
        self.assertIn('src="/workspaces/ws-1/assets/quiz.js"', out)

    def test_rewrites_presigned_s3_asset_urls_to_proxy_paths(self):
        html = (
            '<link rel="stylesheet" '
            'href="https://bucket.s3.amazonaws.com/workspaces/ws-1/assets/lesson.css?X-Amz-Signature=abc">'
        )
        out = rewrite_workspace_asset_refs(html, "ws-1")
        self.assertIn('href="/workspaces/ws-1/assets/lesson.css"', out)
        self.assertNotIn("Signature=", out)


class ValidateWorkspaceFilePathTests(TestCase):
    def test_accepts_allowed_prefixes(self):
        self.assertEqual(
            validate_workspace_file_path("lessons/0001.html"),
            "lessons/0001.html",
        )
        self.assertEqual(
            validate_workspace_file_path("reference/foo.html"),
            "reference/foo.html",
        )
        self.assertEqual(
            validate_workspace_file_path("assets/lesson.css"),
            "assets/lesson.css",
        )

    def test_rejects_path_traversal(self):
        self.assertIsNone(validate_workspace_file_path("../etc/passwd"))
        self.assertIsNone(validate_workspace_file_path("lessons/../../secret.txt"))

    def test_rejects_disallowed_prefix(self):
        self.assertIsNone(validate_workspace_file_path("MISSION.md"))

    def test_accepts_root_resource_markdown(self):
        self.assertEqual(validate_workspace_file_path("RESOURCES.md"), "RESOURCES.md")
        self.assertEqual(validate_workspace_file_path("NOTES.md"), "NOTES.md")


class ContentTypeMappingTests(TestCase):
    def test_maps_common_extensions(self):
        self.assertEqual(
            content_type_for_file_path("lessons/foo.html"),
            "text/html; charset=utf-8",
        )
        self.assertEqual(
            content_type_for_file_path("assets/lesson.css"),
            "text/css; charset=utf-8",
        )
        self.assertEqual(
            content_type_for_file_path("assets/quiz.js"),
            "application/javascript; charset=utf-8",
        )
        self.assertEqual(
            content_type_for_file_path("learning-records/foo.md"),
            "text/plain; charset=utf-8",
        )


class ServeWorkspaceHtmlTests(IsolatedStorageTestCase):
    def test_lesson_html_rewrites_asset_urls(self):
        ws = Workspace.objects.create(title="Test", topic_slug="asset-rewrite")
        ws_id = str(ws.id)
        storage = get_storage()
        storage.write(
            ws_id,
            "lessons/0001-test.html",
            '<html><head><link href="../assets/lesson.css" rel="stylesheet"></head>'
            "<body>Hi</body></html>",
        )
        storage.write(ws_id, "assets/lesson.css", "body { color: red; }")

        response = Client().get(f"/workspaces/{ws_id}/lessons/0001-test.html")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f'href="/workspaces/{ws_id}/assets/lesson.css"',
            response.content.decode(),
        )
        self.assertIn("frame-ancestors", response["Content-Security-Policy"])

    def test_missing_seed_asset_is_backfilled_on_request(self):
        ws = Workspace.objects.create(title="Test", topic_slug="asset-backfill")
        ws_id = str(ws.id)

        response = Client().get(f"/workspaces/{ws_id}/assets/lesson.css")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/css", response["Content-Type"])
        self.assertTrue(get_storage().exists(ws_id, "assets/lesson.css"))
