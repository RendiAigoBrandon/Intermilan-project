class UploadTechnicalError(Exception):
    """Raised for technical issues like unsafe zips, unsupported formats, max size exceeded.
    Files should typically be cleaned up when this is raised."""
    pass


class UploadBusinessLimitError(Exception):
    """Raised for business constraints that represent a hard limit.
    The upload should be rejected, but the source file may be kept until expiration."""
    pass
