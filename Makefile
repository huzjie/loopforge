# LoopForge 开发命令入口
# Windows 用户可用 Git Bash / WSL 执行；所有路径均为相对路径。

PYTHON ?= python
VENV   ?= .venv
ifeq ($(OS),Windows_NT)
BIN := $(VENV)/Scripts
else
BIN := $(VENV)/bin
endif

PY   := $(BIN)/python
PIP  := $(BIN)/pip
PYTEST := $(BIN)/pytest
RUFF := $(BIN)/ruff
MYPY := $(BIN)/mypy
ALEMBIC := $(BIN)/alembic

SRC := src/loopforge
TESTS := tests

.DEFAULT_GOAL := help
.PHONY: help venv install install-dev lint fmt fmt-check typecheck test test-unit \
        cov migrate downgrade heads reset-db seed clean verify all

help: ## 显示可用命令
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

venv: ## 创建虚拟环境
	$(PYTHON) -m venv $(VENV)

install: ## 安装运行时依赖
	$(PIP) install -r requirements.txt

install-dev: ## 安装开发依赖并以可编辑模式安装本包
	$(PIP) install -r requirements-dev.txt
	$(PIP) install -e .

lint: ## ruff 检查
	$(RUFF) check $(SRC) $(TESTS)

fmt: ## ruff 自动格式化
	$(RUFF) format $(SRC) $(TESTS)
	$(RUFF) check --fix $(SRC) $(TESTS)

fmt-check: ## 格式化检查（CI 用）
	$(RUFF) format --check $(SRC) $(TESTS)

typecheck: ## mypy --strict
	$(MYPY) --strict $(SRC)/core $(SRC)/db $(SRC)/models $(SRC)/schemas

test: ## 运行全部测试
	$(PYTEST) $(TESTS)

test-unit: ## 只跑单测
	$(PYTEST) $(TESTS)/unit

cov: ## 带覆盖率运行单测
	$(PYTEST) $(TESTS)/unit --cov=loopforge --cov-report=term-missing --cov-report=xml

migrate: ## 升级到最新迁移
	$(ALEMBIC) upgrade head

downgrade: ## 回退到空库
	$(ALEMBIC) downgrade base

heads: ## 检查迁移头（必须恰好一行）
	$(ALEMBIC) heads

reset-db: ## downgrade base 后重新 upgrade head
	$(ALEMBIC) downgrade base
	$(ALEMBIC) upgrade head

seed: ## 写入种子数据
	$(PY) -c "import asyncio; from loopforge.db.seed import seed_all; asyncio.run(seed_all())"

clean: ## 清理构建与缓存产物
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

verify: lint typecheck test ## 本地全量校验

all: install-dev verify ## 一键装环境并校验
