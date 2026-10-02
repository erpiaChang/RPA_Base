"""ERPia 메인 창 좌측 프로세스바 항목의 auto_id. 화면 모듈 셋이 같이 쓴다.

09-22 정리 — 같은 문자열이 order_mapping / logistics_wait / logistics 에 따로 있었다.
확정 근거는 `docs/CONTROLS.md` "프로세스바" (2026-09-03 덤프).

    Text(auto_id="imgLbl_OrderCollect", name="주문수집")
      <- Pane("lyt_OrderCollect") <- Pane("tbPnl_ProcessBarInside") <- Pane("tbPnl_ProcessBar")
"""
ORDER_COLLECT = "imgLbl_OrderCollect"      # 주문매핑 매출처리 화면
LOGISTICS_WAIT = "imgLbl_HoldLogistics"    # 물류대기 — 없는 계정이 있다 (`logistics_wait.menu_available`)
LOGISTICS = "imgLbl_Logistics"             # 물류관리 (프로세스바 이름은 '물류처리')
