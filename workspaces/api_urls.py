from django.urls import path

from workspaces import views

urlpatterns = [
    path("workspaces/", views.workspaces_collection, name="workspaces-collection"),
    path(
        "workspaces/<uuid:workspace_id>/lessons/",
        views.list_lessons,
        name="workspace-lessons",
    ),
    path(
        "workspaces/<uuid:workspace_id>/lessons/<uuid:lesson_id>/",
        views.get_lesson,
        name="workspace-lesson-detail",
    ),
    path(
        "workspaces/<uuid:workspace_id>/materials/",
        views.list_materials,
        name="workspace-materials",
    ),
    path(
        "workspaces/<uuid:workspace_id>/manifest/",
        views.workspace_manifest,
        name="workspace-manifest",
    ),
    path(
        "workspaces/<uuid:workspace_id>/files/presign/",
        views.presign_workspace_files,
        name="workspace-files-presign",
    ),
    path(
        "workspaces/<uuid:workspace_id>/messages/",
        views.list_messages,
        name="workspace-messages",
    ),
    path(
        "workspaces/<uuid:workspace_id>/chat/",
        views.chat,
        name="workspace-chat",
    ),
    path(
        "workspaces/<uuid:workspace_id>/chat/<uuid:turn_id>/",
        views.get_chat_turn,
        name="workspace-chat-turn",
    ),
]
