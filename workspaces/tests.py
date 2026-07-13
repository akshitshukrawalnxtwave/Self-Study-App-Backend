import json
import shutil
import tempfile
import uuid
from pathlib import Path

from django.test import Client, TestCase, override_settings
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


class IsolatedStorageTestCase(TestCase):
    """Use temporary workspace dirs so tests never write to project workspaces_data/."""

    def setUp(self):
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


class WorkspaceAPITests(IsolatedStorageTestCase):
    def setUp(self):
        super().setUp()

    def tearDown(self):
        super().tearDown()

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

    def test_list_messages_returns_chat_history(self):
        create = self.client.post(
            "/api/workspaces/",
            data=json.dumps({"title": "History", "topic_slug": "history"}),
            content_type="application/json",
        )
        ws_id = create.json()["id"]

        self.client.post(
            f"/api/workspaces/{ws_id}/chat/",
            data=json.dumps({"content": "Hello"}),
            content_type="application/json",
        )

        response = self.client.get(f"/api/workspaces/{ws_id}/messages/")
        self.assertEqual(response.status_code, 200)
        messages = response.json()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["content"], "Hello")
        self.assertEqual(messages[1]["role"], "assistant")
        self.assertIn("id", messages[0])
        self.assertIn("created_at", messages[0])

    def test_list_messages_not_found(self):
        response = self.client.get(f"/api/workspaces/{uuid.uuid4()}/messages/")
        self.assertEqual(response.status_code, 404)

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
        self.assertEqual(first.status_code, 200)
        first_data = first.json()
        self.assertIsNone(first_data["panel"]["html_url"])
        self.assertEqual(len(first_data["messages"]), 1)

        second = self.client.post(
            f"/api/workspaces/{ws_id}/chat/",
            data=json.dumps({"content": "For my exams"}),
            content_type="application/json",
        )
        self.assertEqual(second.status_code, 200)
        second_data = second.json()
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
        response = self.client.get(f"/workspaces/{ws_id}/assets/lesson.css")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/css", response["Content-Type"])

    def test_path_traversal_blocked(self):
        create = self.client.post(
            "/api/workspaces/",
            data=json.dumps({"title": "Bio", "topic_slug": "bio"}),
            content_type="application/json",
        )
        ws_id = create.json()["id"]
        response = self.client.get(f"/workspaces/{ws_id}/../secret.txt")
        self.assertIn(response.status_code, (403, 404))


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
        self.assertEqual(url, f"/workspaces/{self.ws_id}/lessons/0001.html")

    def test_file_url_returns_proxy_path_for_assets(self):
        url = self.storage.file_url(self.ws_id, "assets/lesson.css")
        self.assertEqual(url, f"/workspaces/{self.ws_id}/assets/lesson.css")

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
        self.assertEqual(html_url, f"/workspaces/{ws_id}/lessons/0001-demo.html")

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

        served = http.get(f"/workspaces/{ws_id}/assets/lesson.css")
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

        served = http.get(f"/workspaces/{ws_id}/reference/series-and-dataframe.html")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served["Content-Type"], "text/html; charset=utf-8")

    def test_disallowed_path_prefix_returns_403(self):
        reset_storage()
        http = Client()
        ws_id = str(uuid.uuid4())
        self.storage.write(ws_id, "MISSION.md", "# Mission\n")
        Workspace.objects.create(title="Mission", topic_slug=f"mission-{ws_id[:8]}", id=ws_id)

        response = http.get(f"/workspaces/{ws_id}/MISSION.md")
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

            served = http.get(f"/workspaces/{ws_id}/assets/lesson.css")
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
