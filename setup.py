import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="async-firmata",
    version="0.0.1",
    author="Lennart K",
    description="An asynchronous interface for the Firmata protocol",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/lennart-k/python-async-firmata",
    packages=["async_firmata"],
    classifiers=[
        "Programming Language :: Python :: 3.6",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    install_requires=open("requirements.txt").read().split("\n")
)
