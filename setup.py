from setuptools import setup, find_packages

setup(
    name="coverage-agent",
    version="1.0.0",
    description="Local Agentic RAG Test Coverage Analyzer",
    packages=find_packages(),
    install_requires=[
        "sentence-transformers",
        "torch",
        "rank-bm25",
        "pymilvus",
        "watchdog",
        "pydantic",
        "pyyaml",
        "pypdf",
        "python-docx",
        "xxhash",
        "rich",
        "fastapi",
        "uvicorn",
        "python-multipart",
    ],
    entry_points={
        "console_scripts": [
            "coverage-agent = src.cli:main",
        ],
    },
)
