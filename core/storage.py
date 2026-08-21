"""Static files storage for production cache-busting."""

import logging

from django.contrib.staticfiles.storage import ManifestStaticFilesStorage

logger = logging.getLogger("core")


class ResilientManifestStaticFilesStorage(ManifestStaticFilesStorage):
    """ManifestStaticFilesStorage that degrades to the unhashed name instead of raising.

    Hashed filenames (``app.4e1a9c2b.js``) are what make the ``expires 30d`` cache header
    on ``/static/`` safe: the URL changes whenever the file changes, so a deploy reaches
    returning visitors immediately instead of being masked by their browser cache for a
    month.

    The stock storage raises ``ValueError`` whenever a name is missing from
    ``staticfiles.json`` — including when the manifest does not exist at all, because
    ``collectstatic`` has not run. That turns an asset problem into a hard failure of
    every page that renders ``{% static %}``:

    - the test suite runs with ``DEBUG=False`` and ``staticfiles/`` is gitignored, so a
      fresh checkout would fail every template-rendering test;
    - in production it would mean a site-wide 500 during the window before
      ``collectstatic`` completes.

    ``collectstatic`` writes both the hashed and the original filename, so the unhashed
    URL is always a valid, serveable fallback. Falling back therefore costs only
    cache-busting for that one file, never availability.
    """

    # manifest_strict stays True on purpose. With it False, a name missing from the
    # manifest but present on disk makes Django hash the file on the fly and return
    # app.<hash>.js — a URL that 404s, because only collectstatic writes hashed copies.
    # Strict lookup instead raises on every miss, which stored_name() turns into the
    # plain name: a URL that is always serveable.

    def stored_name(self, name):
        try:
            return super().stored_name(name)
        except ValueError:
            # No manifest entry: collectstatic has not run, or has not caught up with
            # this file yet. Serve the unhashed name, which collectstatic also writes.
            logger.debug("static manifest miss, serving unhashed name: %s", name)
            return name
