"""Version 2 multi-agent orchestration, isolated from the v1 agent."""

__all__ = ["V2Result", "answer_question_v2", "build_supervisor_graph"]


def __getattr__(name):
    if name in __all__:
        from agent.v2.graph import V2Result, answer_question_v2, build_supervisor_graph

        return {
            "V2Result": V2Result,
            "answer_question_v2": answer_question_v2,
            "build_supervisor_graph": build_supervisor_graph,
        }[name]
    raise AttributeError(name)
