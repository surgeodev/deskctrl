from setuptools import setup, find_packages

setup(
    name="deskctrl",
    version="0.2.3",
    description="Remote desktop controller -- cross-platform, like scrcpy for PCs",
    long_description=open("README.md", encoding="utf-8").read() if __import__("os").path.exists("README.md") else "",
    long_description_content_type="text/markdown",
    author="surgeodev",
    url="https://github.com/surgeodev/deskctrl",
    packages=find_packages(),
    include_package_data=True,
    python_requires=">=3.8",
    install_requires=[
        "click>=8.0",
        "mss>=9.0",
        "numpy>=1.21",
        "opencv-python-headless>=4.8",
        "pygame>=2.5",
        "pynput>=1.7",
        "Pillow>=9.0",
    ],
    extras_require={
        "gui": ["PyQt6>=6.5"],
        "discovery": ["zeroconf>=0.131"],
        "all": ["PyQt6>=6.5", "zeroconf>=0.131"],
    },
    entry_points={
        "console_scripts": [
            "deskctrl=deskctrl.cli:cli",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Operating System :: OS Independent",
        "Topic :: System :: Networking",
    ],
)
