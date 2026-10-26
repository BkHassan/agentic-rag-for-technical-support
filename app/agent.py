"""Compatibility entry point. The pipeline lives in app.orchestration."""

from app.orchestration.coordinator import (
    _extract_function_calls,
    _generate_content_with_retry,
    _parse_classification,
    analyze_ticket_with_trace,
    assess_evidence,
    classify_ticket,
    collect_evidence_for_ticket,
    generate_grounded_recommendations,
    handle_support_ticket,
    is_daily_quota_error,
    main,
    retry_delay_seconds,
    run_tool_calling_turn,
)

__all__ = [
    "analyze_ticket_with_trace",
    "classify_ticket",
    "handle_support_ticket",
    "is_daily_quota_error",
    "retry_delay_seconds",
]

if __name__ == "__main__":
    main()
