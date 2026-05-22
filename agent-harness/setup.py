from setuptools import find_namespace_packages, setup


setup(
    name="cli-anything-auto-quota",
    version="0.1.0",
    description="CLI-Anything harness for auto-quota",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    include_package_data=True,
    package_data={
        "cli_anything.auto_quota": ["skills/*.md"],
    },
    install_requires=[
        "click>=8.0",
        "requests>=2.28",
    ],
    entry_points={
        "console_scripts": [
            "cli-anything-auto-quota=cli_anything.auto_quota.auto_quota_cli:cli",
        ],
    },
    python_requires=">=3.10",
)
