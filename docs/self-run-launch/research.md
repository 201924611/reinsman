# STEP 1 — 리서치(경량): OSS 에이전트 도구들이 실제로 쓴 홍보 방법

_2026-07-18. 목적: novelty 게이트의 기준선이 될 '이미 다들 하는 방법' 목록 확정._

## A. 실제 성과 사례 (웹서치 근거)
- **OpenClaw**: 9k→188k 스타 60일, 창작자 OpenAI 합류·Sam Altman 공개 언급·Fortune 피처. → 성장 동력은 **제도적 백킹 + 화제성**(개인 무명 프로젝트가 못 베낌).
- **LangGraph / AutoGen / CrewAI / OpenAI Agents SDK**: 33k~58k 스타. 공통 동력 = **모기업 백킹(MS·OpenAI·LangChain) + 기술적 우수성 + HN/Reddit 커뮤니티 노출 + 월 수백만 다운로드로 증명되는 프로덕션 채택**.
- 핵심 함의: 우리는 개인 무명 프로젝트 → **백킹·화제성 경로는 불가**. 남는 건 **기술적 각도의 독창성 + 커뮤니티 진정성**. 그래서 novelty가 유일한 레버.

## B. 이미 다들 하는 홍보 방법 (novelty 기준선 — 이걸 그대로 하면 ★1)
1. **Show HN / Hacker News 셀프포스트** — "Show HN: I built X". 포화. 무명은 대부분 프론트 못 감.
2. **r/selfhosted · r/LocalLLaMA · r/opensource 포스트** — 셀프호스트/로컬 에이전트 커뮤니티. 흔함.
3. **트위터/X 런치 스레드** — "🧵 I built an autonomous agent that…". 포화, 팔로워 없으면 도달 0.
4. **dev.to / Medium / 개인 블로그 튜토리얼** — "How I built…". 대량 존재(위 검색결과 자체가 그 예).
5. **awesome-list PR** — awesome-ai-agents 류에 항목 추가 PR. 표준 관행, 낮은 도달이나 SEO·발견성엔 유효.
6. **Product Hunt 런치** — 하루 스파이크. 준비물 많고 팔로워 의존.
7. **데모 영상(YouTube/asciinema/GIF)** — README에 GIF. 사실상 필수 위생요소.
8. **Discord/Slack 커뮤니티 공유, 뉴스레터 등재** — TLDR·Ben's Bites류. 큐레이터 의존.
9. **벤치마크/리더보드 등재** — SWE-bench 등. 우리 도메인(범용 자율 하네스)엔 적합 벤치 부재.
10. **비교글("X vs Y") SEO 콘텐츠** — 프레임워크 비교 표. 대량 존재.

→ 위 1~10은 전부 '사람이 프로젝트 *about* 을 쓴다'는 동일 형식. **에이전트 자체는 홍보에 등장하지 않는다.** 여기가 빈틈.

## C. 관찰된 빈틈(차별화 기회)
- 모든 표준 방법은 **정적 산출물(글 1편·스레드·영상)** + **저자=사람**. 독자는 "또 하나의 런치 글"로 소비.
- agent-core의 본질은 **'스스로 일하는 에이전트'**. 그런데 어떤 프로젝트도 **홍보 아티팩트 자체를 에이전트가 실시간으로 만들어내는 모습**을 제품 증명으로 쓰지 않는다. (튜토리얼은 "이렇게 쓰세요"지, "이 글은 제품이 스스로 썼습니다"가 아님.)
- 즉 **매체(medium)와 메시지(message)를 일치**시키는 각도 = 홍보물 = 제품의 실행 로그 = 재현 가능한 증거. 이건 백킹·팔로워 없이도 성립하고, 남이 베끼려면 같은 자율 하네스가 있어야 함.

## STEP 1 DoD ✅
'이미 있는 방법' 목록(B, 10종) + 성과사례(A) + 빈틈(C) 확정.

## Sources
- [The AI Agent Star Race (Medium, 2026-05)](https://medium.com/@rosgluk/the-ai-agent-star-race-i-pulled-live-github-data-for-20-frameworks-in-may-2026-b4919dfba5e4)
- [14 Open Source AI Agent Tools with the Most GitHub Stars (Medium, 2026-06)](https://medium.com/@nocobase/14-open-source-ai-agent-tools-with-the-most-github-stars-bc779661ce0c)
- [Best open source agent frameworks (Firecrawl)](https://www.firecrawl.dev/blog/best-open-source-agent-frameworks)
- [awesome-agents (kyrolabs)](https://github.com/kyrolabs/awesome-agents)
