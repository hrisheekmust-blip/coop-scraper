"""States and the transitions allowed between them."""
from __future__ import annotations

# Application lifecycle
QUEUED = "queued"
RESOLVING = "resolving"
AUTHENTICATING = "authenticating"
PREPARING = "preparing"
FILLING = "filling"
VALIDATING = "validating"
READY = "ready_to_submit"
SUBMITTING = "submitting"
VERIFYING = "verifying"
APPLIED = "applied"
AWAITING_EMAIL = "awaiting_email"
NEEDS_INFO = "needs_information"
NEEDS_HUMAN = "needs_human_verification"
RETRY_WAIT = "retry_wait"
UNCERTAIN = "submission_uncertain"
CLOSED = "closed"
CANCELLED = "cancelled"
FAILED = "failed"

ACTIVE = {RESOLVING, AUTHENTICATING, PREPARING, FILLING, VALIDATING, READY, SUBMITTING, VERIFYING}
RUNNABLE = {QUEUED, RETRY_WAIT}
# Waiting on something outside the worker; resumable without a new application.
PARKED = {AWAITING_EMAIL, NEEDS_INFO, NEEDS_HUMAN}
TERMINAL = {APPLIED, CLOSED, CANCELLED, FAILED}
# States in which the final submit may have happened: never auto-retried.
POST_SUBMIT = {SUBMITTING, VERIFYING, UNCERTAIN}

# What the board shows (spec section 1).
DISPLAY = {
    QUEUED: "Queued", RETRY_WAIT: "Queued",
    RESOLVING: "Applying", AUTHENTICATING: "Applying", PREPARING: "Applying", FILLING: "Applying",
    VALIDATING: "Applying", READY: "Applying", SUBMITTING: "Applying", VERIFYING: "Applying",
    APPLIED: "Applied",
    NEEDS_INFO: "Needs information",
    AWAITING_EMAIL: "Needs verification", NEEDS_HUMAN: "Needs verification",
    UNCERTAIN: "Submission uncertain",
    FAILED: "Failed",
    CLOSED: "Closed", CANCELLED: "Closed",
}

ALLOWED = {
    QUEUED: {RESOLVING, CANCELLED, CLOSED},
    RETRY_WAIT: {RESOLVING, CANCELLED, QUEUED, CLOSED},
    RESOLVING: {AUTHENTICATING, PREPARING, FILLING, CLOSED, RETRY_WAIT, FAILED, NEEDS_INFO, NEEDS_HUMAN, AWAITING_EMAIL, CANCELLED, APPLIED},
    AUTHENTICATING: {PREPARING, FILLING, AWAITING_EMAIL, NEEDS_HUMAN, NEEDS_INFO, RETRY_WAIT, FAILED, CANCELLED, CLOSED},
    PREPARING: {FILLING, AUTHENTICATING, RETRY_WAIT, FAILED, CANCELLED, NEEDS_INFO, CLOSED},
    FILLING: {VALIDATING, AUTHENTICATING, NEEDS_INFO, NEEDS_HUMAN, AWAITING_EMAIL, RETRY_WAIT, FAILED, CANCELLED, CLOSED, FILLING},
    VALIDATING: {READY, FILLING, NEEDS_INFO, NEEDS_HUMAN, RETRY_WAIT, FAILED, CANCELLED},
    READY: {SUBMITTING, FILLING, NEEDS_INFO, RETRY_WAIT, FAILED, CANCELLED},
    SUBMITTING: {VERIFYING, UNCERTAIN, APPLIED},
    VERIFYING: {APPLIED, UNCERTAIN, NEEDS_INFO},
    AWAITING_EMAIL: {QUEUED, CANCELLED, FAILED, NEEDS_HUMAN, CLOSED},
    NEEDS_INFO: {QUEUED, CANCELLED, CLOSED},
    NEEDS_HUMAN: {QUEUED, CANCELLED, CLOSED},
    UNCERTAIN: {APPLIED, QUEUED, CANCELLED},       # QUEUED only through an explicit "did not submit" resolution
    FAILED: {QUEUED, CANCELLED, CLOSED},
    CLOSED: {QUEUED},
    CANCELLED: {QUEUED},
    APPLIED: set(),
}

# Account lifecycle (spec section 6)
ACC_UNKNOWN = "unknown"
ACC_CHECKING = "checking"
ACC_REGISTERING = "registering"
ACC_AWAITING_EMAIL = "awaiting_email"
ACC_ACTIVE = "active"
ACC_EXISTING = "existing_account"
ACC_NEEDS_CREDENTIALS = "needs_credentials"
ACC_NEEDS_MFA = "needs_mfa"
ACC_NEEDS_HUMAN = "needs_human_verification"
ACC_UNCERTAIN = "registration_uncertain"
ACC_LOCKED = "locked"

# Why an attempt stopped short: shown to the user verbatim with the needed action.
BLOCKERS = {
    "missing_fact": "A required question has no saved or derivable answer",
    "credentials_rejected": "The portal rejected the saved username/password",
    "password_policy": "The portal's password rules reject your configured password",
    "username_taken": "The portal says the configured username is taken",
    "existing_account": "An account already exists for your email on this portal; its password is different",
    "credential_destination": "The login page is on a host that isn't verified for this employer",
    "email_verification": "Waiting for the account verification email",
    "login_code": "Waiting for the emailed login code",
    "human_verification": "The portal is showing a captcha or identity check",
    "mfa": "The portal asks for a second factor",
    "sso": "The portal requires a sign-in you have to complete once",
    "consent_unconfigured": "The portal asks for a consent your saved policy doesn't cover",
    "changed_form": "The form changed in a way the worker couldn't follow",
    "no_progress": "The page stopped advancing",
    "upload_failed": "A file upload didn't complete",
    "site_error": "The portal returned an error",
    "posting_closed": "The posting is closed",
    "unsupported": "This page type isn't supported yet",
    "wrong_identity": "The signed-in applicant or job doesn't match",
}
