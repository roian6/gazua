# gazua

동행복권 로또 구매/결과 확인 자동화 스크립트입니다. Slack으로 상태를 알림합니다.

## Requirements
- Python 3.8+
- Playwright 브라우저 설치: `python3 -m playwright install`

## Install
```
pip install -r requirements.txt
```

## Environment
`.env` 파일 또는 환경변수로 설정하세요.

```
DHL_USER_ID=your_id
DHL_USER_PW=your_password
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL=#channel
LOTTO_COUNT=1
HEADLESS=true
DEBUG=false
DEBUG_ARTIFACTS=false
DEBUG_DIR=artifacts
```

## Usage
```
python3 buy_lotto.py
python3 check_result.py
```

Positional 인자도 지원합니다.
```
python3 buy_lotto.py <id> <pw> <slack_token> <slack_channel> <count>
python3 check_result.py <id> <pw> <slack_token> <slack_channel>
```

## Debug artifacts
`DEBUG_ARTIFACTS=true`로 설정하면 오류 시 스크린샷/HTML이 `artifacts/`에 저장됩니다.

## Tests
```
pip install -r requirements-dev.txt
pytest
```
