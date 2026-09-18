from invoke import task


@task
def build_package(c):
    c.run("uv build")
