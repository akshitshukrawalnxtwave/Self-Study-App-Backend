from django.contrib import admin

from workspaces.models import ChatSession, Message, Workspace

admin.site.register(Workspace)
admin.site.register(ChatSession)
admin.site.register(Message)
