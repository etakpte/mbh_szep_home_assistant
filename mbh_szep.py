import appdaemon.plugins.hass.hassapi as hass
import json, time, os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import paho.mqtt.client as mqtt

class MbhSzepBalance(hass.Hass):

    def initialize(self):
        # --- Config from apps.yaml ---
        self.selenium_url = self.args.get("selenium_url")
        self.username = self.args.get("username")
        self.password = self.args.get("password")
        self.target_url = self.args.get("target_url", "https://portal.mbhszepkartya.hu/munkavallalo/")
        self.css_selector = self.args.get(
            "css_selector",
            "#ctl00_ContentPlaceHolder1_grdAlszamlak2_ctl00__0 > td:nth-child(2)"
        )
        self.mqtt_broker = self.args.get("mqtt_broker")
        self.mqtt_port = int(self.args.get("mqtt_port"))
        self.mqtt_user = self.args.get("mqtt_user")
        self.mqtt_pass = self.args.get("mqtt_pass")
        self.mqtt_topic = self.args.get("mqtt_topic", "szep/balance")
        self.poll_interval = int(self.args.get("poll_interval", 6*60*60))

        # --- Setup MQTT ---
        self.mqtt_client = mqtt.Client()
        if self.mqtt_user:
            self.mqtt_client.username_pw_set(self.mqtt_user, self.mqtt_pass)
        try:
            self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port, 60)
            self.mqtt_client.loop_start()
            self.log(f"Connected MQTT to {self.mqtt_broker}:{self.mqtt_port}")
        except Exception as e:
            self.log(f"MQTT connect error: {e}")

        # --- Register service & schedule scraping ---
        self.register_service("mbh/scrape_now", self.service_scrape_now)
        self.run_every(self.scheduled_scrape, "now", self.poll_interval)
        self.log(f"MbhSzepBalance initialized. Scheduled every {self.poll_interval} seconds")
        self.listen_event(self.scrape_callback, "CALL_MBH_SCRAPE")

    def scrape_callback(self, event_type, data, kwargs) -> None:
        self.log("Event scrape requested")
        self.scrape_and_publish()

    def service_scrape_now(self, namespace, domain, service, kwargs):
        self.log("Manual scrape requested")
        self.scrape_and_publish()

    def scheduled_scrape(self, kwargs):
        self.log("Scheduled scrape triggered")
        self.scrape_and_publish()

    def create_driver(self):
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        driver = webdriver.Remote(command_executor=self.selenium_url, options=chrome_options)
        return driver

    def scrape_and_publish(self):
        driver = None
        session_id = None
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                driver = self.create_driver()
                session_id = driver.session_id  # Capture for validation
                self.log(f"New session started: {session_id}")
                
                driver.get(self.target_url)
                wait = WebDriverWait(driver, 15)
    
                # Login sequence
                username_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#ContentPlaceHolder1_txtAzonositoSzam")))
                username_field.clear()
                username_field.send_keys(self.username)
                
                driver.find_element(By.CSS_SELECTOR, "#ContentPlaceHolder1_txtJelszo").send_keys(self.password)
                driver.find_element(By.CSS_SELECTOR, "#ContentPlaceHolder1_cbTipus").click()
                wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#ContentPlaceHolder1_btnBejelentkezes"))).click()
    
                # Tab navigation with retry
                def safe_tab_click():
                    tab = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 
                        "#ctl00_ContentPlaceHolder1_tsReszletek > div > ul > li:nth-child(3) > a > span > span > span")))
                    tab.click()
                
                self.retry_with_stale_handling(safe_tab_click, wait)
    
                # Extract balance
                balance_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, self.css_selector)))
                balance_raw = balance_element.text.strip()
                balance_value = self.parse_balance(balance_raw)
                
                if balance_value is not None:
                    balance_value = int(balance_value)
    
                # Publish
                payload = {
                    "balance_raw": balance_raw,
                    "balance": balance_value,
                    "timestamp": int(time.time())
                }
                self.mqtt_client.publish(self.mqtt_topic, json.dumps(payload), retain=True)
                self.log(f"✅ Published: {payload}")
                return  # Success
    
            except Exception as e:
                self.error(f"Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    self.error("All retries exhausted")
                    return
                time.sleep(2 ** attempt)
                
            finally:
                # Safe cleanup - validate session before quit
                if driver and session_id:
                    try:
                        if driver.session_id == session_id:  # Session still valid
                            driver.quit()
                            self.log(f"Session {session_id} closed cleanly")
                    except Exception as cleanup_error:
                        self.error(f"Cleanup failed (ignored): {cleanup_error}")
                        # Force kill process if needed
                        try:
                            driver.service.process.kill()
                        except:
                            pass
    
    
    
    def retry_with_stale_handling(self, action, wait, max_retries=3):
        """Handle stale elements with session validation."""
        for attempt in range(max_retries):
            try:
                action()
                return True
            except (StaleElementReferenceException, TimeoutException):
                self.log(f"Stale/timeout on attempt {attempt + 1}, retrying...")
                time.sleep(1)
            except Exception as e:
                if "invalid session" in str(e).lower():
                    raise Exception("Session expired during interaction")
        raise Exception("Max retries exceeded")



    def parse_balance(self, raw):
        if raw is None:
            return None
        s = ''.join(c for c in raw if c.isdigit() or c in ",.-")
        if s.count(",") and s.count(".") == 0:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
        try:
            return float(s)
        except Exception:
            try:
                return float(s.replace(" ", ""))
            except:
                return None
