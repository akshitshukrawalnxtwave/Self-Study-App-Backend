import json
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from django.test import Client, TestCase, TransactionTestCase, override_settings
from moto import mock_aws

from workspaces.models import ChatSession, Lesson, Workspace
from workspaces.services.agent import agent_service
from workspaces.storage import (
    agent_cache_is_warm,
    ensure_agent_cache,
    get_storage,
    local_workspace_root,
    prune_orphan_workspace_dirs,
    reset_storage,
)
from workspaces.storage.cloud import CloudWorkspaceStorage
from workspaces.storage.s3 import S3WorkspaceStorage


def _poll_turn(client, workspace_id, turn_id, timeout=10.0):
    """Poll GET chat turn until completed/failed or timeout."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/workspaces/{workspace_id}/chat/{turn_id}/")
        if last.status_code != 200:
            return last
        status = last.json().get("status")
        if status in ("completed", "failed"):
            return last
        time.sleep(0.05)
    return last


class _IsolatedStorageMixin:
    """Use temporary workspace dirs so tests never write to project workspaces_data/."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.mkdtemp()
        self.workspaces_root = Path(self._tmp) / "workspaces_data"
        self.cloud_root = Path(self._tmp) / "workspaces_cloud"
        self._settings = override_settings(
            WORKSPACES_ROOT=self.workspaces_root,
            WORKSPACES_CLOUD_ROOT=self.cloud_root,
        )
        self._settings.enable()
        reset_storage()
        self.client = Client()

    def tearDown(self):
        self._settings.disable()
        reset_storage()
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()


class IsolatedStorageTestCase(_IsolatedStorageMixin, TestCase):
    """Isolated storage with TestCase transaction rollback."""


class IsolatedStorageTransactionTestCase(_IsolatedStorageMixin, TransactionTestCase):
    """Isolated storage with real commits (needed for background chat turns)."""


class WorkspaceAPITests(IsolatedStorageTestCase):
    def test_list_workspaces_empty(self):
        response = self.client.get("/api/workspaces/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_prune_orphan_workspace_dirs(self):
        orphan_id = str(uuid.uuid4())
        orphan_dir = self.workspaces_root / orphan_id
        orphan_dir.mkdir(parents=True)
        (orphan_dir / "lessons").mkdir()

        ws = Workspace.objects.create(title="Keep", topic_slug="keep-me")
        keep_dir = self.workspaces_root / str(ws.id)
        keep_dir.mkdir(parents=True)

        removed = prune_orphan_workspace_dirs()
        self.assertIn(orphan_id, removed)
        self.assertFalse(orphan_dir.exists())
        self.assertTrue(keep_dir.exists())

    def test_create_workspace(self):
        response = self.client.post(
            "/api/workspaces/",
            data=json.dumps(
                {"title": "Fluid Mechanics", "topic_slug": "fluid-mechanics"}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["title"], "Fluid Mechanics")
        self.assertEqual(data["topic_slug"], "fluid-mechanics")

        storage = get_storage()
        self.assertTrue(storage.exists(data["id"], "assets/lesson.css"))
        self.assertTrue(storage.exists(data["id"], "assets/quiz.js"))
        self.assertEqual(ChatSession.objects.filter(workspace_id=data["id"]).count(), 1)

    def test_create_workspace_dedup_returns_existing(self):
        self.client.post(
            "/api/workspaces/",
            data=json.dumps({"title": "Fluid", "topic_slug": "fluid-mechanics"}),
            content_type="application/json",
        )
        response = self.client.post(
            "/api/workspaces/",
            data=json.dumps({"title": "Other", "topic_slug": "fluid-mechanics"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Workspace.objects.count(), 1)

    @override_settings(WORKSPACE_AUTH_REQUIRED=True)
    def test_list_workspaces_requires_auth_when_enabled(self):
        response = self.client.get("/api/workspaces/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "UNAUTHORIZED")

    @override_settings(WORKSPACE_AUTH_REQUIRED=True)
    def test_create_workspace_requires_auth_when_enabled(self):
        response = self.client.post(
            "/api/workspaces/",
            data=json.dumps({"title": "Fluid", "topic_slug": "fluid-mechanics"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    @override_settings(WORKSPACE_AUTH_REQUIRED=True)
    def test_create_workspace_sets_user_id_from_cookie(self):
        user_id = uuid.uuid4()
        self.client.cookies["user_id"] = str(user_id)
        response = self.client.post(
            "/api/workspaces/",
            data=json.dumps({"title": "Fluid", "topic_slug": "fluid-mechanics"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        ws = Workspace.objects.get(pk=response.json()["id"])
        self.assertEqual(ws.user_id, user_id)

    @override_settings(WORKSPACE_AUTH_REQUIRED=True)
    def test_list_workspaces_filters_by_user(self):
        user_a = uuid.uuid4()
        user_b = uuid.uuid4()
        Workspace.objects.create(
            title="A", topic_slug="shared-slug", user_id=user_a
        )
        Workspace.objects.create(
            title="B", topic_slug="shared-slug", user_id=user_b
        )

        self.client.cookies["user_id"] = str(user_a)
        response = self.client.get("/api/workspaces/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["title"], "A")

    @override_settings(WORKSPACE_AUTH_REQUIRED=True)
    def test_create_workspace_dedup_is_per_user(self):
        user_a = uuid.uuid4()
        user_b = uuid.uuid4()

        client_a = Client()
        client_a.cookies["user_id"] = str(user_a)
        first = client_a.post(
            "/api/workspaces/",
            data=json.dumps({"title": "Fluid", "topic_slug": "fluid-mechanics"}),
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 201)

        client_b = Client()
        client_b.cookies["user_id"] = str(user_b)
        second = client_b.post(
            "/api/workspaces/",
            data=json.dumps({"title": "Fluid", "topic_slug": "fluid-mechanics"}),
            content_type="application/json",
        )
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(Workspace.objects.count(), 2)

        again = client_a.post(
            "/api/workspaces/",
            data=json.dumps({"title": "Other", "topic_slug": "fluid-mechanics"}),
            content_type="application/json",
        )
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["id"], first.json()["id"])
        self.assertEqual(Workspace.objects.count(), 2)

    def test_list_lessons_empty(self):
        ws = Workspace.objects.create(title="Test", topic_slug="test-topic")

        response = self.client.get(f"/api/workspaces/{ws.id}/lessons/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_list_lessons_not_found(self):
        response = self.client.get(f"/api/workspaces/{uuid.uuid4()}/lessons/")
        self.assertEqual(response.status_code, 404)

    def test_list_messages_empty(self):
        ws = Workspace.objects.create(title="Test", topic_slug="empty-chat")
        response = self.client.get(f"/api/workspaces/{ws.id}/messages/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_list_messages_not_found(self):
        response = self.client.get(f"/api/workspaces/{uuid.uuid4()}/messages/")
        self.assertEqual(response.status_code, 404)

    def test_get_lesson_not_found(self):
        create = self.client.post(
            "/api/workspaces/",
            data=json.dumps({"title": "Chem", "topic_slug": "chem-lesson-404"}),
            content_type="application/json",
        )
        ws_id = create.json()["id"]
        response = self.client.get(
            f"/api/workspaces/{ws_id}/lessons/{uuid.uuid4()}/"
        )
        self.assertEqual(response.status_code, 404)

    def test_serve_workspace_file(self):
        create = self.client.post(
            "/api/workspaces/",
            data=json.dumps({"title": "Chem", "topic_slug": "chem"}),
            content_type="application/json",
        )
        ws_id = create.json()["id"]
        response = self.client.get(f"/api/workspaces/{ws_id}/assets/lesson.css")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/css", response["Content-Type"])

    def test_path_traversal_blocked(self):
        create = self.client.post(
            "/api/workspaces/",
            data=json.dumps({"title": "Bio", "topic_slug": "bio"}),
            content_type="application/json",
        )
        ws_id = create.json()["id"]
        response = self.client.get(f"/api/workspaces/{ws_id}/../secret.txt")
        self.assertIn(response.status_code, (403, 404))

    def test_workspace_manifest_includes_all_storage_files_and_version_changes(self):
        ws = Workspace.objects.create(title="Manifest", topic_slug="manifest")
        ws_id = str(ws.id)
        storage = get_storage()
        storage.write(ws_id, "lessons/0001-intro.html", "<html>old</html>")
        storage.write(ws_id, "assets/lesson.css", "body {}")
        storage.write_bytes(ws_id, "images/diagram.png", b"png-bytes")

        first = self.client.get(f"/api/workspaces/{ws_id}/manifest/")
        self.assertEqual(first.status_code, 200)
        first_data = first.json()
        by_path = {item["path"]: item for item in first_data["files"]}
        self.assertIn("lessons/0001-intro.html", by_path)
        self.assertIn("assets/lesson.css", by_path)
        self.assertIn("images/diagram.png", by_path)
        self.assertEqual(
            by_path["lessons/0001-intro.html"]["content_type"],
            "text/html; charset=utf-8",
        )

        storage.write(ws_id, "lessons/0001-intro.html", "<html>new</html>")
        second = self.client.get(f"/api/workspaces/{ws_id}/manifest/")
        self.assertNotEqual(
            first_data["workspace_version"],
            second.json()["workspace_version"],
        )

    def test_presign_rejects_invalid_paths(self):
        ws = Workspace.objects.create(title="Presign", topic_slug="presign-invalid")
        response = self.client.post(
            f"/api/workspaces/{ws.id}/files/presign/",
            data=json.dumps({"paths": ["../secret.txt", "/assets/lesson.css"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "VALIDATION_ERROR")

    def test_presign_returns_404_for_missing_paths(self):
        ws = Workspace.objects.create(title="Presign", topic_slug="presign-missing")
        response = self.client.post(
            f"/api/workspaces/{ws.id}/files/presign/",
            data=json.dumps({"paths": ["lessons/missing.html"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["missing_paths"], ["lessons/missing.html"])

    def test_presign_returns_urls_for_existing_paths_only_when_requested(self):
        ws = Workspace.objects.create(title="Presign", topic_slug="presign-success")
        ws_id = str(ws.id)
        storage = get_storage()
        storage.write(ws_id, "lessons/0001.html", "<html></html>")
        storage.write(ws_id, "assets/lesson.css", "body {}")

        response = self.client.post(
            f"/api/workspaces/{ws_id}/files/presign/",
            data=json.dumps({"paths": ["assets/lesson.css"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        urls = response.json()["urls"]
        self.assertEqual(len(urls), 1)
        self.assertEqual(urls[0]["path"], "assets/lesson.css")
        self.assertEqual(urls[0]["url"], f"/api/workspaces/{ws_id}/assets/lesson.css")
        self.assertEqual(urls[0]["expires_in"], 3600)


class WorkspaceChatPollingTests(IsolatedStorageTransactionTestCase):
    """Chat POST/GET polling (background thread needs committed rows)."""

    def test_list_messages_returns_chat_history(self):
        create = self.client.post(
            "/api/workspaces/",
            data=json.dumps({"title": "History", "topic_slug": "history"}),
            content_type="application/json",
        )
        ws_id = create.json()["id"]

        started = self.client.post(
            f"/api/workspaces/{ws_id}/chat/",
            data=json.dumps({"content": "Hello"}),
            content_type="application/json",
        )
        self.assertEqual(started.status_code, 202)
        self.assertEqual(started.json()["status"], "pending")
        turned = _poll_turn(self.client, ws_id, started.json()["turn_id"])
        self.assertEqual(turned.status_code, 200)
        self.assertEqual(turned.json()["status"], "completed")

        response = self.client.get(f"/api/workspaces/{ws_id}/messages/")
        self.assertEqual(response.status_code, 200)
        messages = response.json()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["content"], "Hello")
        self.assertEqual(messages[1]["role"], "assistant")
        self.assertIn("id", messages[0])
        self.assertIn("created_at", messages[0])

    def test_chat_turn_fixture_mode(self):
        create = self.client.post(
            "/api/workspaces/",
            data=json.dumps({"title": "Physics", "topic_slug": "physics"}),
            content_type="application/json",
        )
        ws_id = create.json()["id"]

        first = self.client.post(
            f"/api/workspaces/{ws_id}/chat/",
            data=json.dumps({"content": "I want to learn physics"}),
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json()["status"], "pending")
        first_poll = _poll_turn(self.client, ws_id, first.json()["turn_id"])
        self.assertEqual(first_poll.status_code, 200)
        first_data = first_poll.json()
        self.assertEqual(first_data["status"], "completed")
        self.assertIsNone(first_data["panel"]["html_url"])
        self.assertEqual(len(first_data["messages"]), 1)

        second = self.client.post(
            f"/api/workspaces/{ws_id}/chat/",
            data=json.dumps({"content": "For my exams"}),
            content_type="application/json",
        )
        self.assertEqual(second.status_code, 202)
        second_poll = _poll_turn(self.client, ws_id, second.json()["turn_id"])
        self.assertEqual(second_poll.status_code, 200)
        second_data = second_poll.json()
        self.assertEqual(second_data["status"], "completed")
        self.assertIsNotNone(second_data["panel"]["html_url"])
        self.assertTrue(
            second_data["panel"]["html_url"].endswith("lessons/0001-getting-started.html")
        )
        self.assertTrue(any(a["type"] == "lesson" for a in second_data["artifacts"]))

        lessons = self.client.get(f"/api/workspaces/{ws_id}/lessons/")
        self.assertEqual(lessons.status_code, 200)
        lesson_list = lessons.json()
        self.assertEqual(len(lesson_list), 1)
        self.assertIn("id", lesson_list[0])
        self.assertIn("title", lesson_list[0])
        self.assertIn("path", lesson_list[0])
        self.assertIn("url", lesson_list[0])
        self.assertTrue(
            lesson_list[0]["url"].endswith("lessons/0001-getting-started.html")
        )
        self.assertEqual(Lesson.objects.filter(workspace_id=ws_id).count(), 1)

        lesson_id = lesson_list[0]["id"]
        detail = self.client.get(f"/api/workspaces/{ws_id}/lessons/{lesson_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("html_url", detail.json())
        self.assertTrue(
            detail.json()["html_url"].endswith("lessons/0001-getting-started.html")
        )


class AgentServiceTests(IsolatedStorageTestCase):
    def setUp(self):
        super().setUp()

    def tearDown(self):
        super().tearDown()

    def test_build_sdk_prompt_prefixes_teach(self):
        prompt = agent_service._build_sdk_prompt("I want to learn physics")
        self.assertEqual(prompt, "/teach I want to learn physics")

    def test_build_sdk_options_enables_teach_skill(self):
        workspace = Workspace.objects.create(title="Test", topic_slug="test-skill")
        session = ChatSession.objects.create(workspace=workspace, is_active=True)

        options = agent_service._build_sdk_options(workspace, session)
        self.assertEqual(options.skills, ["teach"])
        self.assertEqual(
            options.allowed_tools,
            ["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
        )

    def test_build_sdk_options_resumes_existing_session(self):
        workspace = Workspace.objects.create(title="Test", topic_slug="test-resume")
        session = ChatSession.objects.create(
            workspace=workspace,
            is_active=True,
            sdk_session_id="existing-sdk-session-id",
        )

        options = agent_service._build_sdk_options(workspace, session)
        self.assertEqual(options.resume, "existing-sdk-session-id")


@mock_aws
@override_settings(
    STORAGE_BACKEND="s3",
    AWS_S3_BUCKET_NAME="test-self-study-bucket",
    AWS_S3_REGION="us-east-1",
    AWS_S3_KEY_PREFIX="workspaces",
    AWS_ACCESS_KEY_ID="testing",
    AWS_SECRET_ACCESS_KEY="testing",
)
class S3WorkspaceStorageTests(IsolatedStorageTestCase):
    def setUp(self):
        super().setUp()
        import boto3

        self.client_s3 = boto3.client("s3", region_name="us-east-1")
        self.client_s3.create_bucket(Bucket="test-self-study-bucket")
        self.storage = S3WorkspaceStorage()
        self.ws_id = str(uuid.uuid4())

    def tearDown(self):
        super().tearDown()

    def test_write_read_exists_list_snapshot(self):
        self.storage.write(self.ws_id, "lessons/0001.html", "<html>hi</html>")
        self.storage.write(self.ws_id, "MISSION.md", "# Mission\n")

        self.assertTrue(self.storage.exists(self.ws_id, "lessons/0001.html"))
        self.assertFalse(self.storage.exists(self.ws_id, "missing.html"))
        html = self.storage.read(self.ws_id, "lessons/0001.html")
        self.assertEqual(html, "<html>hi</html>")
        self.assertEqual(
            self.storage.list(self.ws_id, "lessons"),
            ["lessons/0001.html"],
        )
        snap = self.storage.snapshot(self.ws_id)
        self.assertIn("lessons/0001.html", snap)
        self.assertIn("MISSION.md", snap)

    def test_file_url_returns_proxy_path_for_html(self):
        self.storage.write(self.ws_id, "lessons/0001.html", "<html>hi</html>")
        url = self.storage.file_url(self.ws_id, "lessons/0001.html")
        self.assertEqual(url, f"/api/workspaces/{self.ws_id}/lessons/0001.html")

    def test_file_url_returns_proxy_path_for_assets(self):
        url = self.storage.file_url(self.ws_id, "assets/lesson.css")
        self.assertEqual(url, f"/api/workspaces/{self.ws_id}/assets/lesson.css")

    def test_manifest_file_info_uses_s3_metadata(self):
        self.storage.write(self.ws_id, "assets/lesson.css", "body {}")
        info = self.storage.file_info(self.ws_id, "assets/lesson.css")
        self.assertEqual(info["path"], "assets/lesson.css")
        self.assertEqual(info["size"], len("body {}"))
        self.assertEqual(info["content_type"], "text/css; charset=utf-8")
        self.assertTrue(info["etag"])

    def test_identical_reupload_keeps_same_etag(self):
        self.storage.write(self.ws_id, "assets/lesson.css", "body { color: red; }")
        first = self.storage.file_info(self.ws_id, "assets/lesson.css")["etag"]
        self.storage.write(self.ws_id, "assets/lesson.css", "body { color: red; }")
        second = self.storage.file_info(self.ws_id, "assets/lesson.css")["etag"]
        self.assertEqual(first, second)

        self.storage.write(self.ws_id, "assets/lesson.css", "body { color: blue; }")
        third = self.storage.file_info(self.ws_id, "assets/lesson.css")["etag"]
        self.assertNotEqual(first, third)

    def test_presign_get_url_targets_workspace_object(self):
        self.storage.write(self.ws_id, "lessons/0001.html", "<html>hi</html>")
        url = self.storage.presign_get_url(self.ws_id, "lessons/0001.html", 900)
        self.assertIn("lessons/0001.html", url)
        self.assertIn("Signature", url)

    def test_html_upload_preserves_relative_asset_links(self):
        original = (
            '<link rel="stylesheet" href="../assets/lesson.css">'
            '<script src="../assets/quiz.js"></script>'
        )
        self.storage.write(self.ws_id, "lessons/0001.html", original)
        html = self.storage.read(self.ws_id, "lessons/0001.html")
        self.assertEqual(html, original)

    def test_html_upload_does_not_inject_assets(self):
        original = "<html><head></head><body><p>Hi</p></body></html>"
        self.storage.write(self.ws_id, "lessons/0001.html", original)
        html = self.storage.read(self.ws_id, "lessons/0001.html")
        self.assertEqual(html, original)

    def test_css_upload_sets_text_css_content_type(self):
        self.storage.write_bytes(
            self.ws_id,
            "assets/lesson.css",
            b"body { color: red; }",
        )
        key = self.storage._key(self.ws_id, "assets/lesson.css")
        head = self.client_s3.head_object(Bucket="test-self-study-bucket", Key=key)
        self.assertEqual(head["ContentType"], "text/css; charset=utf-8")

    def test_js_upload_sets_javascript_content_type(self):
        self.storage.write_bytes(
            self.ws_id,
            "assets/quiz.js",
            b"console.log('quiz');",
        )
        key = self.storage._key(self.ws_id, "assets/quiz.js")
        head = self.client_s3.head_object(Bucket="test-self-study-bucket", Key=key)
        self.assertEqual(head["ContentType"], "application/javascript; charset=utf-8")

    def test_get_lesson_returns_proxy_html_url(self):
        reset_storage()
        http = Client()
        create = http.post(
            "/api/workspaces/",
            data=json.dumps({"title": "S3 Lesson", "topic_slug": "s3-lesson-url"}),
            content_type="application/json",
        )
        ws_id = create.json()["id"]
        self.storage.write(
            ws_id,
            "lessons/0001-demo.html",
            "<html><body>demo</body></html>",
        )
        from workspaces.models import Lesson, Workspace

        lesson = Lesson.objects.create(
            workspace=Workspace.objects.get(pk=ws_id),
            path="lessons/0001-demo.html",
            title="Demo",
        )
        detail = http.get(f"/api/workspaces/{ws_id}/lessons/{lesson.id}/")
        self.assertEqual(detail.status_code, 200)
        html_url = detail.json()["html_url"]
        self.assertEqual(html_url, f"/api/workspaces/{ws_id}/lessons/0001-demo.html")

    def test_api_create_and_serve_via_s3(self):
        reset_storage()
        http = Client()
        response = http.post(
            "/api/workspaces/",
            data=json.dumps({"title": "S3 Topic", "topic_slug": "s3-topic"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        ws_id = response.json()["id"]

        storage = get_storage()
        self.assertIsInstance(storage, S3WorkspaceStorage)
        self.assertTrue(storage.exists(ws_id, "assets/lesson.css"))

        served = http.get(f"/api/workspaces/{ws_id}/assets/lesson.css")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served["Content-Type"], "text/css; charset=utf-8")
        self.assertIn("Cache-Control", served)

    def test_serve_reference_html_via_s3(self):
        reset_storage()
        http = Client()
        ws_id = str(uuid.uuid4())
        self.storage.write(
            ws_id,
            "reference/series-and-dataframe.html",
            "<html><body>reference</body></html>",
        )
        Workspace.objects.create(title="Ref", topic_slug=f"ref-{ws_id[:8]}", id=ws_id)

        served = http.get(f"/api/workspaces/{ws_id}/reference/series-and-dataframe.html")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served["Content-Type"], "text/html; charset=utf-8")

    def test_disallowed_path_prefix_returns_403(self):
        reset_storage()
        http = Client()
        ws_id = str(uuid.uuid4())
        self.storage.write(ws_id, "MISSION.md", "# Mission\n")
        Workspace.objects.create(title="Mission", topic_slug=f"mission-{ws_id[:8]}", id=ws_id)

        response = http.get(f"/api/workspaces/{ws_id}/MISSION.md")
        self.assertEqual(response.status_code, 403)


class CloudStorageTests(IsolatedStorageTestCase):
    def setUp(self):
        super().setUp()

    def test_write_read_exists_list_snapshot(self):
        with override_settings(STORAGE_BACKEND="cloud"):
            reset_storage()
            storage = CloudWorkspaceStorage()
            ws_id = str(uuid.uuid4())
            storage.write(ws_id, "lessons/0001.html", "<html>hi</html>")
            storage.write(ws_id, "MISSION.md", "# Mission\n")

            self.assertTrue(storage.exists(ws_id, "lessons/0001.html"))
            self.assertEqual(
                storage.read(ws_id, "lessons/0001.html"), "<html>hi</html>"
            )
            self.assertEqual(
                storage.list(ws_id, "lessons"),
                ["lessons/0001.html"],
            )
            snap = storage.snapshot(ws_id)
            self.assertIn("lessons/0001.html", snap)
            self.assertIn("MISSION.md", snap)

    def test_api_create_and_serve_via_cloud(self):
        with override_settings(STORAGE_BACKEND="cloud"):
            reset_storage()
            http = Client()
            response = http.post(
                "/api/workspaces/",
                data=json.dumps({"title": "Cloud Topic", "topic_slug": "cloud-topic"}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 201)
            ws_id = response.json()["id"]

            storage = get_storage()
            self.assertIsInstance(storage, CloudWorkspaceStorage)
            self.assertTrue(storage.exists(ws_id, "assets/lesson.css"))

            served = http.get(f"/api/workspaces/{ws_id}/assets/lesson.css")
            self.assertEqual(served.status_code, 200)
            self.assertIn("text/css", served["Content-Type"])

    def test_sanitize_seed_content_strips_python_wrappers(self):
        from workspaces.services.seeding import _sanitize_seed_content

        wrapped = '"""body { color: red; }"'
        self.assertEqual(_sanitize_seed_content("lesson.css", wrapped), "body { color: red; }")

        triple = '"""body { margin: 0; }"""'
        self.assertEqual(_sanitize_seed_content("lesson.css", triple), "body { margin: 0; }")

    def test_sync_restores_seed_assets_after_agent_overwrites(self):
        with override_settings(STORAGE_BACKEND="cloud"):
            reset_storage()
            from workspaces.services.seeding import seed_workspace_assets
            from workspaces.storage import sync_agent_cache_to_remote

            storage = CloudWorkspaceStorage()
            ws_id = str(uuid.uuid4())
            seed_workspace_assets(ws_id)

            cache_root = local_workspace_root(ws_id)
            (cache_root / "assets").mkdir(parents=True, exist_ok=True)
            (cache_root / "assets" / "lesson.css").write_text(
                '""" corrupted """', encoding="utf-8"
            )
            (cache_root / "lessons").mkdir(parents=True, exist_ok=True)
            (cache_root / "lessons" / "0001.html").write_text(
                "<html><body>lesson</body></html>", encoding="utf-8"
            )

            sync_agent_cache_to_remote(ws_id)

            css = storage.read(ws_id, "assets/lesson.css")
            self.assertNotIn('""" corrupted """', css)
            self.assertIn("font-family", css)

    def test_agent_cache_skips_rehydrate_when_warm(self):
        with override_settings(STORAGE_BACKEND="cloud", WORKSPACE_AGENT_CACHE_MAX_SIZE=10):
            reset_storage()
            storage = CloudWorkspaceStorage()
            ws_id = str(uuid.uuid4())
            storage.write(ws_id, "assets/lesson.css", "body {}")

            ensure_agent_cache(ws_id)
            self.assertTrue(agent_cache_is_warm(ws_id))

            cache_file = local_workspace_root(ws_id) / "assets" / "lesson.css"
            cache_file.write_text("cached copy", encoding="utf-8")

            ensure_agent_cache(ws_id)
            self.assertEqual(cache_file.read_text(encoding="utf-8"), "cached copy")

    def test_agent_cache_lru_eviction(self):
        with override_settings(STORAGE_BACKEND="cloud", WORKSPACE_AGENT_CACHE_MAX_SIZE=2):
            reset_storage()
            storage = CloudWorkspaceStorage()
            ids = [str(uuid.uuid4()) for _ in range(3)]

            for ws_id in ids[:2]:
                storage.write(ws_id, "assets/lesson.css", f"ws-{ws_id}")

            ensure_agent_cache(ids[0])
            ensure_agent_cache(ids[1])
            self.assertTrue(agent_cache_is_warm(ids[0]))
            self.assertTrue(agent_cache_is_warm(ids[1]))

            storage.write(ids[2], "assets/lesson.css", "ws-3")
            ensure_agent_cache(ids[2])

            self.assertFalse(local_workspace_root(ids[0]).exists())
            self.assertTrue(agent_cache_is_warm(ids[1]))
            self.assertTrue(agent_cache_is_warm(ids[2]))


class AgentBedrockConfigTests(IsolatedStorageTestCase):
    def test_build_sdk_options_includes_bedrock_env(self):
        workspace = Workspace.objects.create(title="Bedrock", topic_slug="bedrock-test")
        session = ChatSession.objects.create(workspace=workspace, is_active=True)
        with override_settings(
            AGENT_PROVIDER="bedrock",
            AGENT_MODEL="sonnet",
            AGENT_SDK_ENV={
                "CLAUDE_CODE_USE_BEDROCK": "1",
                "AWS_REGION": "us-east-1",
                "AWS_ACCESS_KEY_ID": "test-key",
            },
        ):
            options = agent_service._build_sdk_options(workspace, session)
        self.assertEqual(options.model, "sonnet")
        self.assertEqual(options.env["CLAUDE_CODE_USE_BEDROCK"], "1")
        self.assertEqual(options.env["AWS_REGION"], "us-east-1")
        self.assertEqual(options.env["AWS_ACCESS_KEY_ID"], "test-key")

    def test_build_sdk_options_includes_anthropic_aws_env(self):
        workspace = Workspace.objects.create(
            title="Anthropic AWS", topic_slug="anthropic-aws-test"
        )
        session = ChatSession.objects.create(workspace=workspace, is_active=True)
        with override_settings(
            AGENT_PROVIDER="anthropic_aws",
            AGENT_MODEL="sonnet",
            AGENT_SDK_ENV={
                "CLAUDE_CODE_USE_ANTHROPIC_AWS": "1",
                "ANTHROPIC_AWS_WORKSPACE_ID": "proj_test",
                "AWS_REGION": "us-east-1",
            },
        ):
            options = agent_service._build_sdk_options(workspace, session)
        self.assertEqual(options.env["CLAUDE_CODE_USE_ANTHROPIC_AWS"], "1")
        self.assertEqual(options.env["ANTHROPIC_AWS_WORKSPACE_ID"], "proj_test")

    def test_build_sdk_options_includes_mantle_env(self):
        workspace = Workspace.objects.create(title="Mantle", topic_slug="mantle-test")
        session = ChatSession.objects.create(workspace=workspace, is_active=True)
        with override_settings(
            AGENT_PROVIDER="mantle",
            AGENT_MODEL="sonnet",
            AGENT_SDK_ENV={
                "CLAUDE_CODE_USE_MANTLE": "1",
                "AWS_REGION": "us-east-1",
                "ANTHROPIC_AWS_WORKSPACE_ID": "proj_test",
                "ANTHROPIC_BEDROCK_MANTLE_BASE_URL": (
                    "https://bedrock-mantle.us-east-1.api.aws/anthropic"
                ),
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "anthropic.claude-sonnet-5",
            },
        ):
            options = agent_service._build_sdk_options(workspace, session)
        self.assertEqual(options.env["CLAUDE_CODE_USE_MANTLE"], "1")
        self.assertEqual(
            options.env["ANTHROPIC_BEDROCK_MANTLE_BASE_URL"],
            "https://bedrock-mantle.us-east-1.api.aws/anthropic",
        )

    def test_build_sdk_options_omits_bedrock_env_for_anthropic(self):
        workspace = Workspace.objects.create(title="Anthropic", topic_slug="anthropic-test")
        session = ChatSession.objects.create(workspace=workspace, is_active=True)
        with override_settings(AGENT_PROVIDER="anthropic", AGENT_MODEL="sonnet"):
            options = agent_service._build_sdk_options(workspace, session)
        self.assertEqual(options.model, "sonnet")
        self.assertFalse(getattr(options, "env", None))
