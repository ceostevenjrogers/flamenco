import logging

log = logging.getLogger(__name__)

# Mapping from job type to compiler class.
compilers = {}


def register_compiler(job_type):
    """Registers the decorated class as job compiler."""

    def decorator(cls):
        compilers[job_type] = cls
        return cls

    return decorator


# Import subpackages to register the compilers
from . import sleep, blender_render, blender_render_progressive, abstract_compiler


def compile_job(job):
    """Creates tasks from the given job."""

    import datetime
    from bson import tz_util
    from flamenco import current_flamenco

    compiler = construct_job_compiler(job)
    compiler.compile(job)

    # Flip all tasks for this job from 'under-construction' to 'queued', and do the same
    # with the job. This must all happen using a single '_updated' timestamp to prevent
    # race conditions.
    job_id = job['_id']
    now = datetime.datetime.now(tz=tz_util.utc)
    current_flamenco.task_manager.api_set_task_status_for_job(
        job_id, 'under-construction', 'queued', now=now)
    current_flamenco.job_manager.api_set_job_status(job_id, 'queued', now=now)


def validate_job(job):
    """Validates job settings.

    :raises flamenco.exceptions.JobSettingError if the settings are bad.
    """

    compiler = construct_job_compiler(job)
    compiler.validate_job_settings(job)


def construct_job_compiler(job) -> abstract_compiler.AbstractJobCompiler:
    from flamenco import current_flamenco

    compiler_class = find_job_compiler(job)
    compiler = compiler_class(task_manager=current_flamenco.task_manager,
                              job_manager=current_flamenco.job_manager)

    return compiler


def find_job_compiler(job):
    from .abstract_compiler import AbstractJobCompiler

    # Get the compiler class for the job type.
    job_type = job['job_type']
    try:
        compiler_class = compilers[job_type]
    except KeyError:
        log.error('No compiler for job type %r', job_type)
        raise KeyError('No compiler for job type %r' % job_type)

    assert issubclass(compiler_class, AbstractJobCompiler)
    return compiler_class