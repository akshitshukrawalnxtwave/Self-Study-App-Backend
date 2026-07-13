import uuid

from django.test import Client, override_settings

from workspaces.models import LearningMaterial, Workspace
from workspaces.services.materials import sync_materials_from_storage
from workspaces.storage import get_storage
from workspaces.tests import IsolatedStorageTestCase
from workspaces.utils import material_title_from_path
from django.test import TestCase


class MaterialTitleTests(TestCase):
    def test_reference_title_from_filename(self):
        self.assertEqual(
            material_title_from_path("reference/hydrostatics-cheatsheet.html"),
            "Hydrostatics Cheatsheet",
        )

    def test_learning_record_title_from_filename(self):
        self.assertEqual(
            material_title_from_path("learning-records/0001-topic-started.md"),
            "Topic Started",
        )

    def test_root_resource_title(self):
        self.assertEqual(material_title_from_path("RESOURCES.md"), "Resources")


class ListMaterialsTests(IsolatedStorageTestCase):
    def setUp(self):
        super().setUp()
        self.ws = Workspace.objects.create(title="Materials", topic_slug="materials-test")
        self.ws_id = str(self.ws.id)
        self.storage = get_storage()

    def test_list_materials_syncs_from_storage(self):
        self.storage.write(
            self.ws_id,
            "reference/hydrostatics-cheatsheet.html",
            "<html><body>ref</body></html>",
        )
        self.storage.write(
            self.ws_id,
            "learning-records/0001-topic-started.md",
            "# Topic started\n",
        )
        self.storage.write(self.ws_id, "RESOURCES.md", "# Resources\n")

        response = Client().get(f"/api/workspaces/{self.ws_id}/materials/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 3)
        self.assertEqual(data[0]["kind"], "reference")
        self.assertEqual(data[0]["format"], "html")
        self.assertEqual(data[0]["path"], "reference/hydrostatics-cheatsheet.html")
        self.assertEqual(
            data[0]["url"],
            f"/workspaces/{self.ws_id}/reference/hydrostatics-cheatsheet.html",
        )
        self.assertEqual(data[1]["kind"], "learning_record")
        self.assertEqual(data[1]["format"], "markdown")
        self.assertEqual(data[2]["kind"], "resource")
        self.assertEqual(LearningMaterial.objects.filter(workspace=self.ws).count(), 3)

    def test_list_materials_not_found(self):
        response = Client().get(f"/api/workspaces/{uuid.uuid4()}/materials/")
        self.assertEqual(response.status_code, 404)

    def test_serve_root_markdown_via_proxy(self):
        self.storage.write(self.ws_id, "NOTES.md", "# Notes\n")
        response = Client().get(f"/workspaces/{self.ws_id}/NOTES.md")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertIn("# Notes", response.content.decode())

    @override_settings(WORKSPACE_AUTH_REQUIRED=True)
    def test_materials_requires_auth_when_enabled(self):
        response = Client().get(f"/api/workspaces/{self.ws_id}/materials/")
        self.assertEqual(response.status_code, 401)

    @override_settings(WORKSPACE_AUTH_REQUIRED=True)
    def test_materials_allows_cookie_auth(self):
        user_id = uuid.uuid4()
        self.ws.user_id = user_id
        self.ws.save(update_fields=["user_id"])
        client = Client()
        client.cookies["user_id"] = str(user_id)
        response = client.get(f"/api/workspaces/{self.ws_id}/materials/")
        self.assertEqual(response.status_code, 200)

    @override_settings(WORKSPACE_AUTH_REQUIRED=True)
    def test_materials_forbidden_for_other_user(self):
        self.ws.user_id = uuid.uuid4()
        self.ws.save(update_fields=["user_id"])
        other_user = uuid.uuid4()
        client = Client()
        client.cookies["user_id"] = str(other_user)
        response = client.get(f"/api/workspaces/{self.ws_id}/materials/")
        self.assertEqual(response.status_code, 403)


class SyncMaterialsCommandTests(IsolatedStorageTestCase):
    def test_sync_materials_from_storage_backfills_db(self):
        ws = Workspace.objects.create(title="Sync", topic_slug="sync-materials")
        ws_id = str(ws.id)
        storage = get_storage()
        storage.write(ws_id, "reference/foo.html", "<html></html>")

        synced = sync_materials_from_storage(ws)
        self.assertEqual(len(synced), 1)
        self.assertEqual(synced[0].kind, "reference")
        self.assertEqual(synced[0].path, "reference/foo.html")
