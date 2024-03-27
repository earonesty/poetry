from __future__ import annotations

import tempfile

from pathlib import Path
from typing import TYPE_CHECKING

from build import BuildBackendException
from poetry.core.utils.helpers import temporary_directory

from poetry.utils._compat import decode
from poetry.utils.helpers import extractall
from poetry.utils.isolated_build import IsolatedBuildError
from poetry.utils.isolated_build import isolated_builder


if TYPE_CHECKING:
    from poetry.repositories import RepositoryPool
    from poetry.utils.cache import ArtifactCache
    from poetry.utils.env import Env


class ChefError(Exception): ...


class Chef:
    def __init__(
        self, artifact_cache: ArtifactCache, env: Env, pool: RepositoryPool
    ) -> None:
        self._env = env
        self._pool = pool
        self._artifact_cache = artifact_cache

    def prepare(
        self, archive: Path, output_dir: Path | None = None, *, editable: bool = False, config_settings: dict = {}
    ) -> Path:
        if not self._should_prepare(archive):
            return archive

        if archive.is_dir():
            destination = output_dir or Path(tempfile.mkdtemp(prefix="poetry-chef-"))
            return self._prepare(archive, destination=destination, editable=editable, config_settings=config_settings)

        return self._prepare_sdist(archive, destination=output_dir)

    def _prepare(
        self, directory: Path, destination: Path, *, editable: bool = False, config_settings: dict = {}
    ) -> Path:
        from subprocess import CalledProcessError

        distribution = "wheel" if not editable else "editable"
        error: Exception | None = None

        try:
            with isolated_builder(
                source=directory,
                distribution=distribution,
                python_executable=self._env.python,
                pool=self._pool,
            ) as builder:
                return Path(
                    builder.build(
                        distribution,
                        destination.as_posix(),
                        config_settings
                    )
                )
        except BuildBackendException as e:
            message_parts = [str(e)]

            if isinstance(e.exception, CalledProcessError):
                text = e.exception.stderr or e.exception.stdout
                if text is not None:
                    message_parts.append(decode(text))
            else:
                message_parts.append(str(e.exception))

            error = IsolatedBuildError("\n\n".join(message_parts))

        if error is not None:
            raise error from None

    def _prepare_sdist(self, archive: Path, destination: Path | None = None) -> Path:
        from poetry.core.packages.utils.link import Link

        suffix = archive.suffix
        zip = suffix == ".zip"

        with temporary_directory() as tmp_dir:
            archive_dir = Path(tmp_dir)
            extractall(source=archive, dest=archive_dir, zip=zip)

            elements = list(archive_dir.glob("*"))

            if len(elements) == 1 and elements[0].is_dir():
                sdist_dir = elements[0]
            else:
                sdist_dir = archive_dir / archive.name.rstrip(suffix)
                if not sdist_dir.is_dir():
                    sdist_dir = archive_dir

            if destination is None:
                destination = self._artifact_cache.get_cache_directory_for_link(
                    Link(archive.as_uri())
                )

            destination.mkdir(parents=True, exist_ok=True)

            return self._prepare(
                sdist_dir,
                destination,
            )

    def _should_prepare(self, archive: Path) -> bool:
        return archive.is_dir() or not self._is_wheel(archive)

    @classmethod
    def _is_wheel(cls, archive: Path) -> bool:
        return archive.suffix == ".whl"
