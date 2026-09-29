#include <WiFi.h>
#include <WebServer.h>

// ESP32-S3 -> L298N
#define IN1 5
#define IN2 6
#define IN3 7
#define IN4 15

const char* ssid = "ESP32-CAR";
const char* password = "12345678";

WebServer server(80);

// The browser sends every 120 ms. Three missed messages stop the rover.
const unsigned long COMMAND_TIMEOUT = 450;
const unsigned long REVERSE_BRAKE_MS = 35;
unsigned long lastCommandTime = 0;
bool watchdogStopped = true;

enum MotorCommand { MOTOR_STOP, MOTOR_FORWARD, MOTOR_BACKWARD };
MotorCommand leftState = MOTOR_STOP;
MotorCommand rightState = MOTOR_STOP;

void writeLeft(MotorCommand command) {
  digitalWrite(IN1, command == MOTOR_FORWARD ? HIGH : LOW);
  digitalWrite(IN2, command == MOTOR_BACKWARD ? HIGH : LOW);
  leftState = command;
}

void writeRight(MotorCommand command) {
  digitalWrite(IN3, command == MOTOR_FORWARD ? HIGH : LOW);
  digitalWrite(IN4, command == MOTOR_BACKWARD ? HIGH : LOW);
  rightState = command;
}

void stopCar() {
  writeLeft(MOTOR_STOP);
  writeRight(MOTOR_STOP);
}

bool isReversal(MotorCommand before, MotorCommand after) {
  return (before == MOTOR_FORWARD && after == MOTOR_BACKWARD) ||
         (before == MOTOR_BACKWARD && after == MOTOR_FORWARD);
}

void applyCommands(MotorCommand left, MotorCommand right) {
  // Remove power briefly before reversing to reduce spikes and jerking.
  bool reverseLeft = isReversal(leftState, left);
  bool reverseRight = isReversal(rightState, right);
  if (reverseLeft) writeLeft(MOTOR_STOP);
  if (reverseRight) writeRight(MOTOR_STOP);
  if (reverseLeft || reverseRight) delay(REVERSE_BRAKE_MS);
  writeLeft(left);
  writeRight(right);
}

bool parseMotor(String value, MotorCommand& result) {
  value.toUpperCase();
  if (value == "STOP") {
    result = MOTOR_STOP;
    return true;
  }
  if (value == "FORWARD") {
    result = MOTOR_FORWARD;
    return true;
  }
  if (value == "BACKWARD") {
    result = MOTOR_BACKWARD;
    return true;
  }
  return false;
}

// Keep the dual-motor webpage API and also accept the Python camera API.
bool readRequestedCommands(MotorCommand& left, MotorCommand& right) {
  if (server.hasArg("left") && server.hasArg("right")) {
    return parseMotor(server.arg("left"), left) &&
           parseMotor(server.arg("right"), right);
  }

  if (!server.hasArg("throttle") || !server.hasArg("steering")) {
    return false;
  }

  String throttle = server.arg("throttle");
  String steering = server.arg("steering");
  throttle.toUpperCase();
  steering.toUpperCase();

  MotorCommand drive;
  if (!parseMotor(throttle, drive)) return false;
  if (steering != "LEFT" && steering != "CENTER" && steering != "RIGHT") {
    return false;
  }

  if (drive == MOTOR_STOP) {
    left = MOTOR_STOP;
    right = MOTOR_STOP;
  } else {
    left = drive;
    right = drive;
    if (steering == "LEFT") left = MOTOR_STOP;
    if (steering == "RIGHT") right = MOTOR_STOP;
  }
  return true;
}

const char webpage[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
  <title>ESP32 Rover Control</title>
  <style>
    *{box-sizing:border-box;user-select:none;-webkit-user-select:none;touch-action:none}
    body{margin:0;background:#10151b;color:white;font-family:Arial,sans-serif;text-align:center}
    h1{margin:18px 0 4px}.subtitle{color:#72ff95;margin-bottom:18px}
    .joystick-container{display:flex;justify-content:center;gap:25px;flex-wrap:wrap;margin-top:20px}
    .joystick-section{width:165px}.label{font-size:20px;font-weight:bold;margin-bottom:10px}
    .joystick{width:160px;height:260px;background:#272e36;border:3px solid #505963;border-radius:80px;position:relative;margin:auto;box-shadow:inset 0 0 25px #000c,0 5px 20px #0008}
    .center-line{width:120px;height:2px;background:#66717b;position:absolute;left:17px;top:126px}
    .up-text,.down-text{position:absolute;width:100%;color:#7c8790;font-size:13px}.up-text{top:15px}.down-text{bottom:15px}
    .stick{width:75px;height:75px;background:#00a8ff;border-radius:50%;position:absolute;left:40px;top:90px;box-shadow:0 0 20px #00a8ffb3;z-index:5}
    .motor-status{margin-top:10px;font-weight:bold;font-size:18px;color:#00d8ff}
    .buttons{margin-top:28px;display:flex;justify-content:center;gap:15px}
    button{border:0;border-radius:12px;padding:14px 24px;font-size:18px;font-weight:bold;color:white}
    #swapButton{background:#1565c0}#stopButton{background:#d32f2f}
    #swapStatus{margin-top:15px;color:#f6c344;font-size:16px}.info{color:#999;margin-top:18px;font-size:13px}
  </style>
</head>
<body>
  <h1>ESP32-S3 Rover</h1>
  <div class="subtitle">● WiFi Connected</div>
  <div class="joystick-container">
    <div class="joystick-section">
      <div class="label">LEFT</div>
      <div id="leftBase" class="joystick"><div class="center-line"></div><div class="up-text">FORWARD</div><div class="down-text">BACKWARD</div><div id="leftStick" class="stick"></div></div>
      <div id="leftStatus" class="motor-status">STOP</div>
    </div>
    <div class="joystick-section">
      <div class="label">RIGHT</div>
      <div id="rightBase" class="joystick"><div class="center-line"></div><div class="up-text">FORWARD</div><div class="down-text">BACKWARD</div><div id="rightStick" class="stick"></div></div>
      <div id="rightStatus" class="motor-status">STOP</div>
    </div>
  </div>
  <div class="buttons"><button id="swapButton" onclick="swapControls()">⇄ SWAP</button><button id="stopButton" onclick="emergencyStop()">STOP</button></div>
  <div id="swapStatus">Normal Control</div>
  <div class="info">Hold joystick continuously to drive.<br>Release joystick to stop that motor.</div>

<script>
let leftCommand="STOP",rightCommand="STOP",controlsSwapped=false;
let requestInFlight=false,pendingSend=false;
const maxY=82,deadZone=22;

function currentUrl(){
  let left=leftCommand,right=rightCommand;
  if(controlsSwapped){left=rightCommand;right=leftCommand}
  return "/control?left="+encodeURIComponent(left)+"&right="+encodeURIComponent(right);
}

async function flushControl(){
  if(requestInFlight)return;
  requestInFlight=true;
  do{
    pendingSend=false;
    const url=currentUrl();
    const aborter=new AbortController();
    const timer=setTimeout(()=>aborter.abort(),300);
    try{await fetch(url,{cache:"no-store",signal:aborter.signal})}
    catch(error){console.log("ESP communication lost")}
    finally{clearTimeout(timer)}
  }while(pendingSend);
  requestInFlight=false;
}

function sendControl(){pendingSend=true;void flushControl()}

function setupJoystick(baseId,stickId,statusId,side){
  const base=document.getElementById(baseId),stick=document.getElementById(stickId),status=document.getElementById(statusId);
  let dragging=false;
  function setCommand(command){
    const old=side==="LEFT"?leftCommand:rightCommand;
    if(side==="LEFT")leftCommand=command;else rightCommand=command;
    status.textContent=command;
    if(command!==old)sendControl();
  }
  function moveStick(event){
    if(!dragging)return;
    event.preventDefault();
    const rect=base.getBoundingClientRect();
    let y=event.clientY-rect.top-rect.height/2;
    y=Math.max(-maxY,Math.min(maxY,y));
    stick.style.top=(90+y)+"px";
    setCommand(y < -deadZone ? "FORWARD" : y > deadZone ? "BACKWARD" : "STOP");
  }
  function resetStick(){
    if(!dragging && (side==="LEFT"?leftCommand:rightCommand)==="STOP")return;
    dragging=false;
    stick.style.top="90px";
    setCommand("STOP");
  }
  base.addEventListener("pointerdown",event=>{dragging=true;base.setPointerCapture(event.pointerId);moveStick(event)});
  base.addEventListener("pointermove",moveStick);
  base.addEventListener("pointerup",resetStick);
  base.addEventListener("pointercancel",resetStick);
  base.addEventListener("lostpointercapture",resetStick);
}

setupJoystick("leftBase","leftStick","leftStatus","LEFT");
setupJoystick("rightBase","rightStick","rightStatus","RIGHT");
setInterval(sendControl,120);

function swapControls(){
  controlsSwapped=!controlsSwapped;
  document.getElementById("swapStatus").textContent=controlsSwapped?"Controls Swapped":"Normal Control";
  sendControl();
}

function emergencyStop(){
  leftCommand=rightCommand="STOP";
  document.getElementById("leftStick").style.top="90px";
  document.getElementById("rightStick").style.top="90px";
  document.getElementById("leftStatus").textContent="STOP";
  document.getElementById("rightStatus").textContent="STOP";
  sendControl();
}

window.addEventListener("blur",emergencyStop);
window.addEventListener("pagehide",()=>{emergencyStop();fetch(currentUrl(),{keepalive:true,cache:"no-store"}).catch(()=>{})});
document.addEventListener("visibilitychange",()=>{if(document.hidden)emergencyStop()});
</script>
</body>
</html>
)rawliteral";

void sendNoCache(int status, const char* text) {
  server.sendHeader("Cache-Control", "no-store, no-cache, must-revalidate");
  server.send(status, "text/plain", text);
}

void handleControl() {
  MotorCommand left;
  MotorCommand right;
  if (!readRequestedCommands(left, right)) {
    stopCar();
    watchdogStopped = true;
    sendNoCache(400, "Invalid or missing motor command");
    return;
  }

  applyCommands(left, right);
  lastCommandTime = millis();
  watchdogStopped = false;
  sendNoCache(200, "OK");
}

void setup() {
  Serial.begin(115200);
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);
  stopCar();

  WiFi.mode(WIFI_AP);
  WiFi.setSleep(false);
  WiFi.softAP(ssid, password);

  server.on("/", []() {
    server.sendHeader("Cache-Control", "no-store");
    server.send_P(200, "text/html", webpage);
  });
  server.on("/control", handleControl);
  server.begin();
  lastCommandTime = millis();

  Serial.println("ESP32-S3 ROVER READY");
  Serial.print("Open: http://");
  Serial.println(WiFi.softAPIP());
}

void loop() {
  server.handleClient();
  if (!watchdogStopped && millis() - lastCommandTime > COMMAND_TIMEOUT) {
    stopCar();
    watchdogStopped = true;
    Serial.println("COMMAND TIMEOUT - STOPPED");
  }
}
