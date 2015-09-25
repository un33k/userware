from django.core.urlresolvers import reverse_lazy
from django.contrib import messages
from django.utils.translation import ugettext_lazy as _
from django.views.generic import TemplateView
from django.views.generic import FormView
from django.views.generic import DeleteView
from django.contrib.sites.models import get_current_site
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.utils.http import is_safe_url
from django.shortcuts import resolve_url
from django.http import HttpResponseRedirect

from toolware.utils.mixin import LoginRequiredMixin
from toolware.utils.mixin import StaffRequiredMixin
from toolware.utils.mixin import CsrfProtectMixin
from toolware.utils.mixin import NeverCacheMixin
from toolware.utils.mixin import SensitivePostParametersMixin

from auditware.utils import force_logout

from .forms import UserPasswordChangeForm
from .forms import UserAuthenticationForm
from .forms import UserDeletionForm
from .forms import UserSwitchForm
from .signals import user_switched_on

from . import defaults as defs
from . import utils as util


class UserAccountView(LoginRequiredMixin, TemplateView):
    """
    Router for account settings or main page.
    """
    def get(self, *args, **kwargs):
        if defs.LOGIN_REDIRECT_URL:
            return HttpResponseRedirect(defs.LOGIN_REDIRECT_URL)
        return HttpResponseRedirect('/')


class UserLogoutView(TemplateView):
    """
    Logout and redirect to LOGOUT_REDIRECT_URL.
    """
    def get(self, request, *args, **kwargs):
        if defs.USERWARE_SWTICHED_USER_KEY in request.session:
            del request.session[defs.USERWARE_SWTICHED_USER_KEY]
        if request.user.is_authenticated():
            auth_logout(request)
            messages.add_message(self.request, messages.SUCCESS, _('You are logged out'))
        return HttpResponseRedirect(defs.LOGOUT_REDIRECT_URL)


class UserLoginView(SensitivePostParametersMixin, CsrfProtectMixin,
    NeverCacheMixin, FormView):
    """
    Login view.
    """
    form_class = UserAuthenticationForm
    success_url = defs.LOGIN_REDIRECT_URL
    extra_context = {}

    redirect_field_name = REDIRECT_FIELD_NAME

    def get_template_names(self):
        template_name = util.get_template_path("login_form.html")
        return template_name

    def get_success_url(self):
        redirect_to = self.request.REQUEST.get(self.redirect_field_name, '')
        if not is_safe_url(url=redirect_to, host=self.request.get_host()):
            redirect_to = resolve_url(defs.LOGIN_REDIRECT_URL)
        return redirect_to or None

    def get_form_kwargs(self):
        kwargs = super(UserLoginView, self).get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def form_valid(self, form):
        auth_login(self.request, form.get_user())
        if self.request.session.test_cookie_worked():
            self.request.session.delete_test_cookie()
        messages.add_message(self.request, messages.SUCCESS,
                    _("You are now logged in as '%s'." % self.request.user.username))
        return super(UserLoginView, self).form_valid(form)

    def get_context_data(self, **kwargs):
        context = super(UserLoginView, self).get_context_data(**kwargs)
        current_site = get_current_site(self.request)
        context.update({
            self.redirect_field_name: self.get_success_url(),
            "site": current_site,
            "site_name": current_site.name,
        })

        context.update(self.extra_context)
        return context

    def get(self, request, *args, **kwargs):
        self.request.session.set_test_cookie()
        if request.user.is_authenticated():
            return HttpResponseRedirect(defs.LOGIN_REDIRECT_URL)
        return super(UserLoginView, self).get(request, *args, **kwargs)


class UserChangePassword(SensitivePostParametersMixin, CsrfProtectMixin,
    LoginRequiredMixin, NeverCacheMixin, FormView):
    """
    Change password for existing user.
    """
    form_class = UserPasswordChangeForm
    success_url = defs.LOGIN_REDIRECT_URL
    message_text = {
        'success': _('Your password changed.'),
        'warning': _('Changing your password will log you out of all of your other sessions.'),
    }

    def get_template_names(self):
        template_name = util.get_template_path("password_change_form.html")
        return template_name

    def get_form_kwargs(self):
        kwargs = super(UserChangePassword, self).get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.save()
        force_logout(self.request.user, self.request)
        messages.add_message(self.request, messages.SUCCESS, self.message_text['success'])
        return super(UserChangePassword, self).form_valid(form)

    def form_invalid(self, form):
        messages.add_message(self.request, messages.WARNING, self.message_text['warning'])
        return super(UserChangePassword, self).form_invalid(form)

    def get(self, request, *args, **kwargs):
        if len(messages.api.get_messages(self.request)) < 1:
            messages.add_message(self.request, messages.WARNING, self.message_text['warning'])
        return super(UserChangePassword, self).get(request, *args, **kwargs)


class UserDeleteView(LoginRequiredMixin, CsrfProtectMixin, FormView):
    """
    Delete an account.
    """
    form_class = UserDeletionForm
    success_url = reverse_lazy('home_page')
    delete_warning = _("This is extremely important. If you delete your account, there is no going back")

    def get_template_names(self):
        template_name = util.get_template_path("delete_account_form.html")
        return template_name

    def get_form_kwargs(self):
        kwargs = super(UserDeleteView, self).get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.add_message(self.request, messages.SUCCESS,
                _("Account '%s' was permanently deleted. Sorry to see you go!" % self.request.user.username))
        self.request.user.delete()
        return super(UserDeleteView, self).form_valid(form)

    def form_invalid(self, form):
        messages.add_message(self.request, messages.WARNING, self.delete_warning)
        return super(UserDeleteView, self).form_invalid(form)

    def get(self, request, *args, **kwargs):
        messages.add_message(self.request, messages.WARNING, self.delete_warning)
        return super(UserDeleteView, self).get(request, *args, **kwargs)


class UserSwitchOnView(LoginRequiredMixin, StaffRequiredMixin,
    CsrfProtectMixin, FormView):
    """
    Switch user id. AKA `su`.
    """
    form_class = UserSwitchForm
    success_url = defs.LOGIN_REDIRECT_URL

    def get_template_names(self):
        template_name = util.get_template_path("switch_user_form.html")
        return template_name

    def form_valid(self, form):
        switched_username = form.cleaned_data['switched_username']
        messages.add_message(self.request, messages.SUCCESS,
                            _("switched to user '%s'" % switched_username))
        self.request.session[defs.USERWARE_SWTICHED_USER_KEY] = switched_username
        user_switched_on.send(sender=self.request.user, switched_username=form.cleaned_data['switched_username'])
        return super(UserSwitchOnView, self).form_valid(form)

    def get(self, request, *args, **kwargs):
        if len(messages.api.get_messages(self.request)) < 1:
            messages.add_message(self.request, messages.WARNING,
                    _("To switch back to a privileged user, you must re-login. This is done for security reasons."))
        return super(UserSwitchOnView, self).get(request, *args, **kwargs)
