from django.urls import path
from vault import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("setup/", views.setup, name="setup"),
    path("login/", views.sign_in, name="login"),
    path("logout/", views.sign_out, name="logout"),
    path("forgot-password/", views.password_reset, name="password_reset"),
    path("reset/<uidb64>/<token>/", views.ResetConfirmView.as_view(), name="password_reset_confirm"),
    path("invite/<str:token>/", views.accept_invitation, name="accept_invitation"),
    path("account/", views.account, name="account"),
    path("collections/", views.collections, name="collections"),
    path("upload/", views.upload_page, name="upload"),
    path("collections/new/", views.collection_create, name="collection_create"),
    path("collections/<uuid:pk>/", views.collection_detail, name="collection"),
    path("collections/<uuid:pk>/edit/", views.collection_edit, name="collection_edit"),
    path("collections/<uuid:pk>/upload/", views.collection_upload, name="collection_upload"),
    path("collections/<uuid:pk>/delete/", views.collection_delete, name="collection_delete"),
    path("collections/<uuid:pk>/link/", views.link_create, name="link_create"),
    path("links/", views.links, name="links"),
    path("links/<uuid:pk>/revoke/", views.link_revoke, name="link_revoke"),
    path("activity/", views.activity, name="activity"),
    path("branding/", views.branding, name="branding"),
    path("storage/", views.storage, name="storage"),
    path("storage/delete/", views.storage_delete, name="storage_delete"),
    path("team/", views.team, name="team"),
    path("team/invitations/<uuid:pk>/revoke/", views.invitation_revoke, name="invitation_revoke"),
    path("team/members/<int:pk>/toggle/", views.member_toggle, name="member_toggle"),
    path("s/<str:token>/", views.share, name="share"),
    path("s/<str:token>/unlock/", views.unlock, name="unlock"),
    path("s/<str:token>/download/", views.download, name="download"),
    path("health/", views.health, name="health"),
    path("robots.txt", lambda request: views.HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")),
]
handler404 = "vault.views.error404"
handler500 = "vault.views.error500"
