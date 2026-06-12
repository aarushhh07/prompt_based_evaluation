from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

# 1. Instantiate
judge_metric = GEval(
    name="Tone Judge",
    evaluation_steps=["Check if tone is excited."],
    evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
    threshold=0.8 # Minimum passing score
)

test_case = LLMTestCase(input="Write an exciting email", actual_output="Subject: Congrats! We are so excited")

# 2. Execute (This returns None)
judge_metric.measure(test_case)

# 3. Extract the outputs directly from the object
output_payload = {
    "score": judge_metric.score,               # Float: 1.0
    "reasoning": judge_metric.reason,          # String: "The email perfectly matched the tone..."
    "passed": judge_metric.is_successful()     # Boolean: True (because 1.0 >= 0.8)
}

print(output_payload)