import os
import sys
import json
from playwright.sync_api import sync_playwright

def run():
    proxy_address = os.getenv("PROXY_ADDRESS")
    proxy_user = os.getenv("PROXY_USER")
    proxy_pw = os.getenv("PROXY_PW")

    if not proxy_address:
        print("Error: PROXY_ADDRESS environment variable is missing")
        return

    print(f"Testing Proxy Server: {proxy_address}")

    with sync_playwright() as p:
        # 1. Direct Connection (GitHub Actions IP)
        print("\n" + "="*50)
        print("[1] Direct Connection (No Proxy)")
        print("="*50)
        try:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("https://api.myip.com", timeout=10000)
            content = page.locator("body").inner_text()
            print(f"Response: {content}")
            browser.close()
        except Exception as e:
            print(f"Direct connection failed: {e}")

        # 2. Proxy Connection
        print("\n" + "="*50)
        print("[2] Proxy Connection")
        print("="*50)
        try:
            proxy_config = {"server": f"http://{proxy_address}"}
            if proxy_user and proxy_pw:
                proxy_config["username"] = proxy_user
                proxy_config["password"] = proxy_pw
            
            print("Launching browser with proxy...")
            browser = p.chromium.launch(headless=True, proxy=proxy_config)
            page = browser.new_page()
            
            print("Navigating to IP check service...")
            page.goto("https://api.myip.com", timeout=30000)
            content = page.locator("body").inner_text()
            print(f"Response: {content}")
            
            try:
                data = json.loads(content)
                country = data.get("cc", "") or data.get("country", "")
                print(f"\nDetected Country: {country}")
                
                if country == "KR":
                    print("✅ SUCCESS: Proxy works and location is Korea!")
                else:
                    print(f"⚠️ WARNING: Proxy works but location is {country} (Not KR)")
            except:
                print("Could not parse JSON response")
                
            browser.close()
        except Exception as e:
            print(f"\n❌ FAILED: Proxy connection failed.")
            print(f"Error details: {e}")
            sys.exit(1)

if __name__ == "__main__":
    run()
