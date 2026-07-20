import uuid

from django.db import models


class Workspace(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField(null=True, blank=True)
    title = models.CharField(max_length=255)
    topic_slug = models.SlugField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_panel_html_url = models.CharField(max_length=512, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def to_dict(self):
        """Serialize the workspace for API responses."""
        return {
            "id": str(self.id),
            "title": self.title,
            "topic_slug": self.topic_slug,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
        }


class ChatSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="sessions"
    )
    last_active_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    sdk_session_id = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-last_active_at"]


class Message(models.Model):
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        ChatSession, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=20)
    message_type = models.CharField(max_length=20, default="text")
    content = models.TextField()
    turn_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def to_dict(self):
        """Serialize the message for API responses."""
        return {
            "id": str(self.id),
            "role": self.role,
            "type": self.message_type,
            "content": self.content,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
        }


class ChatTurn(models.Model):
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="turns"
    )
    session = models.ForeignKey(
        ChatSession, on_delete=models.CASCADE, related_name="turns"
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    user_content = models.TextField()
    result = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    error_code = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def to_status_dict(self) -> dict:
        """Serialize turn status for polling; includes result when completed."""
        payload = {
            "turn_id": str(self.id),
            "status": self.status,
        }
        if self.status == self.STATUS_COMPLETED and self.result is not None:
            payload.update(self.result)
        elif self.status == self.STATUS_FAILED:
            payload["error"] = self.error_message
            payload["code"] = self.error_code or "INTERNAL_ERROR"
        return payload


class Lesson(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="lessons"
    )
    title = models.CharField(max_length=255)
    path = models.CharField(max_length=512)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "path"],
                name="unique_lesson_path_per_workspace",
            )
        ]

    @property
    def html_url(self) -> str:
        """Frontend-facing proxy URL for this lesson's HTML."""
        from workspaces.services.lessons import lesson_html_url

        return lesson_html_url(str(self.workspace_id), self.path)

    def to_list_dict(self) -> dict:
        """Compact serialization for the lesson list endpoint."""
        return {
            "id": str(self.id),
            "title": self.title,
            "path": self.path,
            "url": self.html_url,
        }

    def to_detail_dict(self) -> dict:
        """Full serialization including the lesson HTML URL."""
        return {"id": str(self.id), "title": self.title, "html_url": self.html_url}


class LearningMaterial(models.Model):
    KIND_REFERENCE = "reference"
    KIND_LEARNING_RECORD = "learning_record"
    KIND_RESOURCE = "resource"

    FORMAT_HTML = "html"
    FORMAT_MARKDOWN = "markdown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="materials"
    )
    path = models.CharField(max_length=512)
    kind = models.CharField(max_length=32)
    format = models.CharField(max_length=16)
    title = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "path"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "path"],
                name="unique_material_path_per_workspace",
            )
        ]

    def to_dict(self, workspace_id: str | None = None) -> dict:
        """Serialize for the learning materials list API."""
        from workspaces.storage.urls import workspace_file_url

        ws_id = workspace_id or str(self.workspace_id)
        return {
            "kind": self.kind,
            "path": self.path,
            "url": workspace_file_url(ws_id, self.path),
            "title": self.title,
            "format": self.format,
        }
