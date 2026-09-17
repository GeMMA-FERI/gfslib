# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) but modified for easier processing,
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Release 0.4.0 - 2026-09-17

### Added
- Add parameter to ignore missing files when batch downloading.
- Remote path inspection, renaming, and directory creation/removal.
- Directory downloads, optional path preservation, and SHA-256 verification.
- Remote-to-local sync, include/exclude patterns, and structured sync plans.
- Workspace, dataset, and datasource discovery through `GFClient`.
- Streaming multifile uploads and explicit `upload_file`/`upload_text` helpers.

### Fixed
- `Path` uploads and error propagation for metadata and sync failures.
- Batch filename collisions, quoted metadata parsing, and partial download writes.

### Improved
- Enforce 100% statement and branch coverage in the offline unit suite.

### Compatibility
- `metadata()` now raises on HTTP failures and malformed JSON.
- Batch downloads still flatten paths by default, but reject duplicate names.
- Missing local sync directories now raise instead of appearing empty.

## Release 0.3.1 - 2026-07-06

### Improved
 - Delete method can now handle multiple files/folders in a single request.
 - Improved unit tests code coverage to 100%.

## Release 0.3.0 - 2026-06-28

### Added
 - Support for downloading multiple files in a single request.
 - Add pytest-cov as a development dependency for code coverage reporting.

## Release 0.2.0 - 2026-05-14

### Added
 - **API Changes:** Support for recursive listing of files and directories in StorageServices.
 - **API Changes:** Option to include directory entries in listing responses when supported by the server.
 - Documentation for the library

## Release 0.1.0 - 2026-01-12

### Added
 - Initial repository setup.
 - StorageServices class for handling file uploads and downloads to/from a remote storage service.
 - Tests for StorageServices class.

