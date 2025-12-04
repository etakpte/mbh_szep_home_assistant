# MBH SZÉP kártya egyenleg home assistanthoz

Alapvetően egy AppDaemon script ami meghív egy Selenium-ban futó Chrome böngészőt és begyűjti az egyenleget majd MQTT szerverre feltölti ez értéket.
Home Assistant MQTT topic-bol frissiti a szenzor értékét.

A poll_interval-ban beállitott időközönkét frissit magátol vagy CALL_MBH_SCRAP event hatására kézzel is lehet frissiteni.

## Szükséges addon-ok
 - AppDaemon
 - Selenium (https://github.com/davida72/selenium-homeassistant)
 - MQTT server (Pl: Mosquitto broker https://github.com/home-assistant/addons/tree/master/mosquitto)

## Install:
1. app.yaml-t ki kell tölteni az MBH/MQTT bejelentkezési adatokkal
2. apps.yaml-t és mbh_szep.yaml-t fel kell tölteni /addon_configs/a0d7b954_appdaemon/apps/ könyvtárba
3. AppDaemon config fülön 'Edit in yaml'
```
system_packages: []
python_packages:
  - requests
  - selenium
  - paho-mqtt
init_commands: []
```
4. configuration.yaml-ben a szenzor létrehozása:
```
mqtt:
  sensor:
    - name: "MBH SZÉP Balance"
      state_topic: "szep/balance"
      value_template: "{{ value_json.balance }}"   # The numeric balance
      json_attributes_topic: "szep/balance" 
      unit_of_measurement: "HUF"
```
5. Home assistant ujrainditás
