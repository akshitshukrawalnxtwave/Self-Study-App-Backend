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
        "workspaces/<uuid:workspace_id>/messages/",
        views.list_messages,
        name="workspace-messages",
    ),
    path(
        "workspaces/<uuid:workspace_id>/chat/",
        views.chat,
        name="workspace-chat",
    ),
]
