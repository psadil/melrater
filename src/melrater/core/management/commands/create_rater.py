"""Create a reviewer account, or an ingest account, with an issued password.

Reviewers get an ordinary, non-staff account and a password generated here;
they never choose one and never reset one, which is why config/urls.py routes
no password_change or password_reset view. The generated password is stronger
than a chosen one and there is no unauthenticated reset endpoint to attack —
the cost is that the password travels over whatever channel you send it on,
and only you can rotate it (`--reset`).

`--ingest` makes the same kind of account and adds it to the `ingest` group,
which is what `push_runs` authenticates as. A group rather than `is_staff`,
which would additionally open /admin/, and rather than a bespoke token, which
would have bypassed django-axes: an ingest password that goes through
django.contrib.auth is throttled by the same lockout as any other login.
Revoke one by taking the account out of the group, or by deactivating it.
"""

import typing as t

import typer

# the concrete model rather than get_user_model(): AUTH_USER_MODEL is not
# swapped here, and get_user_model() is typed as the abstract base, whose
# plain Manager has no create_user for ty to find
from django.contrib.auth.models import Group, User
from django.utils.crypto import get_random_string
from django_typer.management import TyperCommand

from melrater.core.api import INGEST_GROUP

#: No 0/O, 1/l/I: these get read aloud, retyped, and pasted out of chat
#: clients that helpfully change the font.
ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"

#: 20 characters of the above is ~116 bits, which no amount of login
#: throttling has to compensate for.
DEFAULT_LENGTH = 20


class Command(TyperCommand):
    def handle(
        self,
        username: t.Annotated[str, typer.Argument(help="The reviewer's login name.")],
        length: t.Annotated[
            int, typer.Option(help="Generated password length.")
        ] = DEFAULT_LENGTH,
        reset: t.Annotated[
            bool,
            typer.Option(help="Issue a new password for an account that exists."),
        ] = False,
        ingest: t.Annotated[
            bool,
            typer.Option(help="Also grant this account run-ingest rights."),
        ] = False,
    ) -> None:
        """Create an account and print its generated password."""
        if length < 12:
            raise typer.BadParameter("password length must be at least 12")
        existing = User.objects.filter(username=username).first()
        if existing is not None and not reset:
            raise typer.BadParameter(
                f"user {username!r} already exists; pass --reset to issue a new password"
            )
        if existing is None and reset:
            raise typer.BadParameter(f"no user {username!r} to reset")

        password = get_random_string(length, ALPHABET)
        if existing is None:
            # Never staff and never a superuser. An admin account can read,
            # rewrite and delete every other reviewer's ratings; and is_staff on
            # its own carries no model permissions, so it would only grant a
            # login to an empty admin. `createsuperuser` is the one way in.
            user = User.objects.create_user(username=username, password=password)
            action = "created"
        else:
            user = existing
            user.set_password(password)
            user.save(update_fields=["password"])
            action = "password reset for"

        if ingest:
            group, _ = Group.objects.get_or_create(name=INGEST_GROUP)
            user.groups.add(group)

        kind = "ingest account" if ingest else "reviewer"
        self.stdout.write(
            self.style.SUCCESS(f"{action} {user.get_username()} ({kind})")
        )
        self.stdout.write("")
        self.stdout.write(f"  username  {user.get_username()}")
        self.stdout.write(f"  password  {password}")
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "Shown once. Send it over a channel you trust, then clear your "
                "scrollback — there is no reset page, so re-issuing means "
                "running this again with --reset."
            )
        )
        if ingest:
            self.stdout.write(
                "This account can push runs. `push_runs` will prompt for the "
                "password, so it need not be stored anywhere."
            )
