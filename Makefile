.PHONY: inject audit run test clean help

## inject: Inject synthetic anomalies into the raw dataset
inject:
	python scripts/inject_anomalies.py

## audit: Run the AI Data Quality Auditor against injected data
audit:
	python main.py

## run: Inject anomalies then run the full audit pipeline
run: inject audit

## test: Run the test suite with coverage reporting
test:
	pytest tests/ -v --cov=auditor

## clean: Remove generated artifacts (injected data, cache, reports) — preserves reports/sample/
clean:
	@echo "Removing injected data..."
	@rm -rf data/injected/
	@echo "Removing LLM response cache..."
	@rm -rf .cache/llm_responses/
	@echo "Removing generated reports (JSON and Markdown)..."
	@find reports/ -maxdepth 1 -name "*.json" -delete 2>/dev/null || true
	@find reports/ -maxdepth 1 -name "*.md" -delete 2>/dev/null || true
	@echo "Clean complete. reports/sample/ was preserved."

## help: Show this help message
help:
	@echo ""
	@echo "AI Data Quality Auditor — available make targets:"
	@echo ""
	@grep -E '^## ' Makefile | sed 's/^## /  /'
	@echo ""
