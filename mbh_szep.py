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
#        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        driver = webdriver.Remote(command_executor=self.selenium_url, options=chrome_options)
        return driver

    def scrape_and_publish(self):
        driver = None
        try:
            driver = self.create_driver()
            driver.get(self.target_url)

            wait = WebDriverWait(driver, 15)

            # --- Fill login form ---
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#ContentPlaceHolder1_txtAzonositoSzam")))
            driver.find_element(By.CSS_SELECTOR, "#ContentPlaceHolder1_txtAzonositoSzam").send_keys(self.username)
            driver.find_element(By.CSS_SELECTOR, "#ContentPlaceHolder1_txtJelszo").send_keys(self.password)

            # Select client type 'Munkavállaló'
            driver.find_element(By.CSS_SELECTOR, "#ContentPlaceHolder1_cbTipus").click()

            # Click login
            driver.find_element(By.CSS_SELECTOR, "#ContentPlaceHolder1_btnBejelentkezes").click()
            
            time.sleep(1)  # small wait for the tab content to render

            tab_selector = "#ctl00_ContentPlaceHolder1_tsReszletek > div > ul > li:nth-child(3) > a > span > span > span"
            tab = driver.find_element(By.CSS_SELECTOR, tab_selector)
            
            tab.click()
            time.sleep(1)  # small wait for the tab content to render


            # --- Wait for balance table to appear ---
            balance_element = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, self.css_selector))
            )
            balance_raw = balance_element.text.strip()
            balance_value = self.parse_balance(balance_raw)
            if balance_value is not None:
                balance_value = int(balance_value)  # remove decimal

            # --- Publish via MQTT ---
            payload = {
                "balance_raw": balance_raw,
                "balance": balance_value,
                "timestamp": int(time.time())
            }
            self.mqtt_client.publish(self.mqtt_topic, json.dumps(payload), retain=True)
            self.log(f"Published balance to MQTT {self.mqtt_topic}: {payload}")

        except Exception as e:
            self.error(f"Scraping failed: {e}")
        finally:
            if driver:
                driver.quit()

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
