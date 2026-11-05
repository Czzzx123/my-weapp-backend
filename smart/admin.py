from django.contrib import admin

from .models import Welcome, Banner, Notice, UserInfo

admin.site.register(Welcome)
admin.site.register(Banner)
admin.site.register(Notice)
admin.site.register(UserInfo)
