"""채널(메신저) 어댑터 계층.

하네스 아키텍처의 'Channels' 기둥. 외부 대화 프런트(텔레그램 등)를
agent-core HTTP API(goal/tasks)에 잇는 얇은 브리지들을 둔다.
엔진(server/orchestrator)과 분리돼 있어, 프런트는 갈아끼울 수 있다.
"""
