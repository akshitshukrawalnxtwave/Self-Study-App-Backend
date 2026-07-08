import json
import uuid

from django.test import Client, TestCase, override_settings
from moto import mock_aws

from workspaces.models import ChatSession, Workspace
from workspaces.services.agent import agent_service
from workspaces.storage import get_storage, reset_storage
from workspaces.storage.s3 import S3WorkspaceStorage


class WorkspaceAPITests(TestCase):
    def setUp(self):
        self.client = Client()
        reset_storage()

    def tearDown(self):
        reset_storage()

    def test_list_workspaces_empty(self):
        response = self.client.get("/api/workspaces/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

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
        seed = get_storage()
        seed.ensure_workspace(str(ws.id))

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


class AgentServiceTests(TestCase):
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
class S3WorkspaceStorageTests(TestCase):
    def setUp(self):
        import boto3

        reset_storage()
        self.client_s3 = boto3.client("s3", region_name="us-east-1")
        self.client_s3.create_bucket(Bucket="test-self-study-bucket")
        self.storage = S3WorkspaceStorage()
        self.ws_id = str(uuid.uuid4())

    def tearDown(self):
        reset_storage()

    def test_write_read_exists_list_snapshot(self):
        self.storage.write(self.ws_id, "lessons/0001.html", "<html>hi</html>")
        self.storage.write(self.ws_id, "MISSION.md", "# Mission\n")

        self.assertTrue(self.storage.exists(self.ws_id, "lessons/0001.html"))
        self.assertFalse(self.storage.exists(self.ws_id, "missing.html"))
        self.assertEqual(
            self.storage.read(self.ws_id, "lessons/0001.html"), "<html>hi</html>"
        )
        self.assertEqual(
            self.storage.list(self.ws_id, "lessons"),
            ["lessons/0001.html"],
        )
        snap = self.storage.snapshot(self.ws_id)
        self.assertIn("lessons/0001.html", snap)
        self.assertIn("MISSION.md", snap)

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
        self.assertIn("text/css", served["Content-Type"])
