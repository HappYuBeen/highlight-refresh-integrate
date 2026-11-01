"""safe_call 화면 테스트 (커밋하지 않음)
실행: poetry run python -m tests.try_logger → http://127.0.0.1:7860
"""
import time
import gradio as gr
from hri.util.logger import safe_call


@safe_call
def on_compare(text):
	return 10 / int(text)


@safe_call
def on_upload(n):
	for i in range(int(n)):
		if i == 2:
			raise RuntimeError("차트 추출 실패")
		yield f"단계 {i + 1} 진행 중..."
		time.sleep(0.5)
	yield "완료"


@safe_call
def on_select(evt: gr.SelectData):
	return f"선택한 줄: {evt.index[0]}"


with gr.Blocks() as demo:
	number = gr.Textbox(label="숫자", value="0")
	run_btn = gr.Button("일반 함수 실행")
	result = gr.Textbox(label="결과")
	steps = gr.Textbox(label="단계 수", value="5")
	run_gen_btn = gr.Button("제너레이터 실행")
	progress = gr.Markdown()
	table = gr.Dataframe(value=[["A"], ["B"]], headers=["제목"], interactive=False)
	selected = gr.Markdown()

	run_btn.click(fn=on_compare, inputs=number, outputs=result)
	run_gen_btn.click(fn=on_upload, inputs=steps, outputs=progress)
	table.select(fn=on_select, outputs=selected)

demo.launch(theme=gr.themes.Soft())