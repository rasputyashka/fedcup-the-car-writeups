const $ = (id) => document.getElementById(id);
const ecuNames = {
  infotainment: "Мультимедиа",
  abs: "Тормоза · ABS",
  engine: "Двигатель",
  autopilot: "Автопилот",
  telemetry: "Телеметрия",
};
const statusNames = { ok: "В норме", hacked: "Изменён", down: "Нет связи" };
const statusColors = { ok: 0x94bba0, hacked: 0xe39488, down: 0x9ca6aa };
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
let selectedEcu = "engine";
let currentState = null;
let refreshMarkers = () => {};
let selectMarker = () => {};

// Telemetry updates keep these controls mounted, preserving keyboard focus.
const ecuRows = new Map();
for (const [name, title] of Object.entries(ecuNames)) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ecu";
  button.setAttribute("aria-pressed", String(name === selectedEcu));
  const label = document.createElement("span");
  label.className = "ecu-name";
  label.textContent = title;
  const status = document.createElement("span");
  status.className = "ecu-status";
  const dot = document.createElement("span");
  dot.className = "status-dot";
  dot.setAttribute("aria-hidden", "true");
  const text = document.createElement("span");
  text.textContent = "Ожидание";
  status.append(dot, text);
  button.append(label, status);
  button.addEventListener("click", () => {
    selectedEcu = name;
    drawInspector();
    selectMarker(name);
  });
  $("ecus").append(button);
  ecuRows.set(name, { button, text, dot });
}

function drawInspector() {
  for (const [name, row] of ecuRows) {
    const status = currentState?.ecus?.[name];
    row.button.setAttribute("aria-pressed", String(name === selectedEcu));
    row.text.textContent = statusNames[status] || "Ожидание";
    row.dot.dataset.status = status || "unknown";
  }
  $("log-title").textContent = "Журнал · " + ecuNames[selectedEcu];
  const events = currentState?.ecu_logs?.[selectedEcu];
  const text =
    Array.isArray(events) && events.length
      ? events.map(String).join("\n")
      : currentState
        ? "Событий пока нет."
        : "Данные ещё не получены.";
  const output = $("log-output");
  if (output.textContent !== text) {
    const atBottom =
      output.scrollHeight - output.scrollTop - output.clientHeight < 24;
    output.textContent = text;
    if (atBottom) output.scrollTop = output.scrollHeight;
  }
  refreshMarkers();
}

function setTelemetry(state) {
  if (!state || typeof state !== "object" || Array.isArray(state)) return;
  currentState = state;
  const speed = typeof state.speed === "number" ? state.speed : NaN;
  const heading = typeof state.heading === "number" ? state.heading : NaN;
  $("speed").textContent = Number.isFinite(speed)
    ? (speed * 3.6).toFixed(1) + " км/ч"
    : "-";
  $("heading").textContent = Number.isFinite(heading)
    ? Math.round((heading * 180) / Math.PI) + "°"
    : "-";
  $("signal").textContent =
    { 0: "Выкл.", 1: "Левый", 2: "Правый", 3: "Аварийный" }[
      state.turn_signal
    ] || "-";
  drawInspector();
}

function connectionState(online) {
  const text = online ? "Автомобиль на связи" : "Нет подключения";
  if ($("connection").textContent !== text) $("connection").textContent = text;
  $("connection").dataset.connected = String(online);
  $("speed").closest("footer")?.classList.toggle("telemetry-stale", !online);
  if (!online) {
    $("speed").textContent = "-";
    $("heading").textContent = "-";
    $("signal").textContent = "-";
    if (currentState) {
      currentState = null;
      drawInspector();
    }
  }
}

// View controls are local. The viewer only reads the existing state endpoints.
let socket = null;
let lastMessageAt = 0;
let pollTimer = null;
let closed = false;
function connectStream() {
  if (closed || (socket && socket.readyState < WebSocket.CLOSING)) return;
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const stream = new WebSocket(protocol + "//" + location.host + "/ws/state");
  socket = stream;
  stream.addEventListener("message", (event) => {
    try {
      const state = JSON.parse(event.data);
      if (!state || typeof state !== "object" || Array.isArray(state)) return;
      setTelemetry(state);
      lastMessageAt = performance.now();
      connectionState(true);
    } catch {
      /* Keep the last readable snapshot if a frame is malformed. */
    }
  });
  stream.addEventListener("error", () => stream.close());
}
async function pollState() {
  if (closed) return;
  if (
    !document.hidden &&
    (socket?.readyState !== WebSocket.OPEN ||
      performance.now() - lastMessageAt > 5000)
  ) {
    try {
      const response = await fetch("/api/state", {
        cache: "no-store",
        signal: AbortSignal.timeout(4000),
      });
      if (!response.ok) throw new Error("State unavailable");
      const state = await response.json();
      if (!state || typeof state !== "object" || Array.isArray(state))
        throw new Error("Invalid state");
      setTelemetry(state);
      connectionState(true);
      connectStream();
    } catch {
      connectionState(false);
    }
  }
  pollTimer = setTimeout(pollState, 2000);
}
window.addEventListener("pagehide", () => {
  closed = true;
  clearTimeout(pollTimer);
  socket?.close();
});
window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    closed = false;
    pollState();
  }
});
drawInspector();
pollState();

async function startViewer() {
  const [THREE, { OrbitControls }, { createCar }] = await Promise.all([
    import("/static/vendor/three.module.js"),
    import("/static/vendor/OrbitControls.js"),
    import("/static/car-model.js"),
  ]);
  const canvas = $("scene");
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: false,
  });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setClearColor(0x202326);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 60);
  const controls = new OrbitControls(camera, canvas);
  controls.target.set(0, 0.68, 0);
  controls.enableDamping = !reducedMotion.matches;
  controls.dampingFactor = 0.08;
  controls.enablePan = false;
  controls.minDistance = 4.2;
  controls.maxDistance = 13;
  controls.minPolarAngle = Math.PI * 0.12;
  controls.maxPolarAngle = Math.PI * 0.485;
  controls.autoRotateSpeed = 0.65;
  controls.zoomSpeed = 0.7;

  // Softbox reflections describe paint and chrome without external images.
  const environmentCanvas = document.createElement("canvas");
  environmentCanvas.width = 1024;
  environmentCanvas.height = 512;
  const context = environmentCanvas.getContext("2d");
  context.fillStyle = "#5c6469";
  context.fillRect(0, 0, 1024, 512);
  context.fillStyle = "#252b30";
  context.fillRect(0, 270, 1024, 242);
  context.fillStyle = "#f2efdf";
  context.fillRect(80, 75, 320, 100);
  context.fillStyle = "#d5e1e5";
  context.fillRect(640, 115, 260, 130);
  context.fillStyle = "#afb9be";
  context.fillRect(0, 0, 1024, 35);
  const texture = new THREE.CanvasTexture(environmentCanvas);
  texture.mapping = THREE.EquirectangularReflectionMapping;
  texture.colorSpace = THREE.SRGBColorSpace;
  const pmrem = new THREE.PMREMGenerator(renderer);
  const environment = pmrem.fromEquirectangular(texture);
  scene.environment = environment.texture;
  texture.dispose();
  pmrem.dispose();

  scene.add(new THREE.HemisphereLight(0xe6edf0, 0x393731, 1.1));
  const keyLight = new THREE.DirectionalLight(0xfff3db, 2.2);
  keyLight.position.set(3.5, 7, 4.5);
  keyLight.castShadow = true;
  keyLight.shadow.mapSize.set(2048, 2048);
  Object.assign(keyLight.shadow.camera, {
    left: -4,
    right: 4,
    top: 4,
    bottom: -4,
    near: 0.5,
    far: 18,
  });
  keyLight.shadow.normalBias = 0.025;
  keyLight.shadow.bias = -0.0001;
  keyLight.shadow.radius = 3;
  scene.add(keyLight);
  const fillLight = new THREE.DirectionalLight(0xd3e3ed, 0.9);
  fillLight.position.set(-4, 4, -5);
  scene.add(fillLight);
  const frontLight = new THREE.DirectionalLight(0xffffff, 0.7);
  frontLight.position.set(5, 2, -1);
  scene.add(frontLight);

  const platform = new THREE.Mesh(
    new THREE.CylinderGeometry(2.85, 2.85, 0.06, 128),
    new THREE.MeshStandardMaterial({
      color: 0x303639,
      roughness: 0.88,
      metalness: 0.08,
    }),
  );
  platform.position.y = -0.032;
  platform.receiveShadow = true;
  scene.add(platform);
  const platformEdge = new THREE.Mesh(
    new THREE.TorusGeometry(2.82, 0.008, 6, 128),
    new THREE.MeshStandardMaterial({ color: 0x62696a, roughness: 0.7 }),
  );
  platformEdge.rotation.x = Math.PI / 2;
  platformEdge.position.y = 0.002;
  scene.add(platformEdge);
  const shadow = new THREE.Mesh(
    new THREE.PlaneGeometry(200, 200),
    new THREE.ShadowMaterial({ opacity: 0.16 }),
  );
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.y = -0.067;
  shadow.receiveShadow = true;
  scene.add(shadow);

  const car = createCar(THREE);
  scene.add(car.group);
  let dirty = true;
  let wheelsSpinning = false;
  let steeringTarget = 0;
  let steering = 0;
  let cameraTransition = null;
  let activeView = "perspective";
  let viewDistance = 7.2;
  const cameraViews = {
    perspective: new THREE.Vector3(1, 0.48, 1.12).normalize(),
    front: new THREE.Vector3(1, 0.17, 0).normalize(),
    side: new THREE.Vector3(0, 0.16, 1).normalize(),
    rear: new THREE.Vector3(-1, 0.2, -0.03).normalize(),
  };

  function updateViewButtons() {
    document.querySelectorAll("[data-view]").forEach((button) => {
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.view === activeView),
      );
    });
  }
  function stopOrbit() {
    controls.autoRotate = false;
    $("auto-rotate").setAttribute("aria-pressed", "false");
  }
  function setView(view, immediate = false) {
    stopOrbit();
    activeView = view;
    updateViewButtons();
    const destination = cameraViews[view]
      .clone()
      .multiplyScalar(viewDistance)
      .add(controls.target);
    if (immediate || reducedMotion.matches) {
      cameraTransition = null;
      camera.position.copy(destination);
      controls.update();
    } else {
      cameraTransition = {
        from: camera.position.clone(),
        to: destination,
        start: performance.now(),
      };
    }
    dirty = true;
  }
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  $("auto-rotate").addEventListener("click", () => {
    controls.autoRotate = !controls.autoRotate;
    $("auto-rotate").setAttribute("aria-pressed", String(controls.autoRotate));
    if (controls.autoRotate) {
      activeView = null;
      cameraTransition = null;
      updateViewButtons();
    }
    dirty = true;
  });
  $("reset-view").addEventListener("click", () => setView("perspective"));
  controls.addEventListener("start", () => {
    stopOrbit();
    activeView = null;
    cameraTransition = null;
    updateViewButtons();
  });
  controls.addEventListener("change", () => {
    dirty = true;
  });
  canvas.addEventListener("keydown", (event) => {
    const keys = [
      "ArrowLeft",
      "ArrowRight",
      "ArrowUp",
      "ArrowDown",
      "+",
      "=",
      "-",
      "_",
      "Home",
    ];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Home") return setView("perspective");
    stopOrbit();
    activeView = null;
    cameraTransition = null;
    updateViewButtons();
    const spherical = new THREE.Spherical().setFromVector3(
      camera.position.clone().sub(controls.target),
    );
    if (event.key === "ArrowLeft") spherical.theta -= 0.12;
    if (event.key === "ArrowRight") spherical.theta += 0.12;
    if (event.key === "ArrowUp") spherical.phi -= 0.1;
    if (event.key === "ArrowDown") spherical.phi += 0.1;
    if (["+", "="].includes(event.key)) spherical.radius *= 0.9;
    if (["-", "_"].includes(event.key)) spherical.radius *= 1.1;
    spherical.phi = THREE.MathUtils.clamp(
      spherical.phi,
      controls.minPolarAngle,
      controls.maxPolarAngle,
    );
    spherical.radius = THREE.MathUtils.clamp(
      spherical.radius,
      controls.minDistance,
      controls.maxDistance,
    );
    camera.position.setFromSpherical(spherical).add(controls.target);
    controls.update();
    dirty = true;
  });

  function setSteering(value) {
    steeringTarget = THREE.MathUtils.degToRad(value);
    $("steering").value = String(value);
    $("steering-value").textContent = (value > 0 ? "+" : "") + value + "°";
    if (reducedMotion.matches) steering = steeringTarget;
    dirty = true;
  }
  $("steering").addEventListener("input", (event) =>
    setSteering(Number(event.target.value)),
  );
  $("center-steering").addEventListener("click", () => setSteering(0));
  $("wheel-spin").addEventListener("click", () => {
    wheelsSpinning = !wheelsSpinning;
    $("wheel-spin").setAttribute("aria-pressed", String(wheelsSpinning));
    dirty = true;
  });
  $("headlights").addEventListener("click", () => {
    const enabled = $("headlights").getAttribute("aria-pressed") !== "true";
    $("headlights").setAttribute("aria-pressed", String(enabled));
    car.setHeadlights(enabled);
    dirty = true;
  });
  document.querySelectorAll("[data-color]").forEach((button) => {
    button.addEventListener("click", () => {
      car.setColor(button.dataset.color);
      document.querySelectorAll("[data-color]").forEach((swatch) => {
        swatch.setAttribute("aria-pressed", String(swatch === button));
      });
      $("paint-name").textContent =
        button.dataset.colorName || button.getAttribute("aria-label");
      dirty = true;
    });
  });

  // Location markers are optional so the default view leaves the body visible.
  const markerLayer = new THREE.Group();
  markerLayer.visible = false;
  car.group.add(markerLayer);
  const markerLayout = {
    infotainment: [0.45, 1.24, -0.35],
    abs: [1.18, 0.7, 0.98],
    engine: [1.35, 1.03, 0],
    autopilot: [-0.05, 1.55, 0.2],
    telemetry: [-1.55, 1.02, 0],
  };
  const markers = new Map();
  const markerGeometry = new THREE.SphereGeometry(0.065, 16, 12);
  for (const [name, position] of Object.entries(markerLayout)) {
    const group = new THREE.Group();
    group.position.set(...position);
    group.userData = { name, base: new THREE.Vector3(...position) };
    const material = new THREE.MeshBasicMaterial({
      color: 0x9ca6aa,
      depthTest: false,
    });
    const point = new THREE.Mesh(markerGeometry, material);
    point.renderOrder = 4;
    group.add(point);
    const labelCanvas = document.createElement("canvas");
    labelCanvas.width = 384;
    labelCanvas.height = 80;
    const labelContext = labelCanvas.getContext("2d");
    labelContext.fillStyle = "#191d20";
    labelContext.fillRect(0, 0, 384, 80);
    labelContext.fillStyle = "#edf0ef";
    labelContext.font = "500 32px Segoe UI, sans-serif";
    labelContext.textAlign = "center";
    labelContext.textBaseline = "middle";
    labelContext.fillText(ecuNames[name], 192, 40);
    const labelTexture = new THREE.CanvasTexture(labelCanvas);
    labelTexture.colorSpace = THREE.SRGBColorSpace;
    const label = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: labelTexture, depthTest: false }),
    );
    label.scale.set(0.96, 0.2, 1);
    label.position.y = 0.25;
    label.renderOrder = 5;
    group.add(label);
    markerLayer.add(group);
    markers.set(name, { group, point, label, material });
  }
  refreshMarkers = () => {
    for (const [name, marker] of markers) {
      marker.material.color.setHex(
        name === selectedEcu
          ? 0x70c8dc
          : statusColors[currentState?.ecus?.[name]] || 0x9ca6aa,
      );
      marker.point.scale.setScalar(name === selectedEcu ? 1.35 : 1);
      marker.label.visible = name === selectedEcu;
    }
    dirty = true;
  };
  function showMarkers(visible) {
    markerLayer.visible = visible;
    $("show-systems").setAttribute("aria-pressed", String(visible));
    dirty = true;
  }
  selectMarker = () => {
    showMarkers(true);
    refreshMarkers();
  };
  $("show-systems").addEventListener("click", () =>
    showMarkers(!markerLayer.visible),
  );
  $("explode").addEventListener("input", (event) => {
    const amount = Number(event.target.value);
    $("explode-value").textContent = Math.round(amount * 100) + "%";
    for (const { group } of markers.values()) {
      const direction = group.userData.base
        .clone()
        .normalize()
        .multiplyScalar(amount * 0.85);
      group.position.copy(group.userData.base).add(direction);
    }
    showMarkers(true);
  });
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let pointerDown = null;
  canvas.addEventListener("pointerdown", (event) => {
    pointerDown = [event.clientX, event.clientY];
  });
  canvas.addEventListener("pointerup", (event) => {
    if (
      !markerLayer.visible ||
      !pointerDown ||
      Math.hypot(
        event.clientX - pointerDown[0],
        event.clientY - pointerDown[1],
      ) > 5
    )
      return;
    pointerDown = null;
    const rect = canvas.getBoundingClientRect();
    pointer.set(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      (-(event.clientY - rect.top) / rect.height) * 2 + 1,
    );
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(
      [...markers.values()].map((marker) => marker.point),
    )[0];
    if (!hit) return;
    selectedEcu = hit.object.parent.userData.name;
    $("diagnostics").open = true;
    drawInspector();
  });
  canvas.addEventListener("pointercancel", () => {
    pointerDown = null;
  });

  function resize() {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    const previousDistance = viewDistance;
    viewDistance = Math.max(7.6, 7.5 / camera.aspect);
    controls.maxDistance = Math.max(13, viewDistance * 1.5);
    if (activeView) setView(activeView, true);
    else if (previousDistance !== viewDistance) {
      camera.position
        .sub(controls.target)
        .multiplyScalar(viewDistance / previousDistance)
        .add(controls.target);
    }
    dirty = true;
  }
  new ResizeObserver(resize).observe(canvas);
  resize();
  refreshMarkers();
  car.setHeadlights(false);

  reducedMotion.addEventListener("change", () => {
    controls.enableDamping = !reducedMotion.matches;
    if (reducedMotion.matches) {
      stopOrbit();
      wheelsSpinning = false;
      $("wheel-spin").setAttribute("aria-pressed", "false");
      if (cameraTransition) camera.position.copy(cameraTransition.to);
      cameraTransition = null;
      steering = steeringTarget;
    }
    dirty = true;
  });
  canvas.addEventListener("webglcontextlost", (event) => {
    event.preventDefault();
    showViewerError("Графический контекст потерян. Перезагрузите модель.");
  });
  let previousFrame = performance.now();
  function frame(now) {
    requestAnimationFrame(frame);
    const delta = Math.min((now - previousFrame) / 1000, 0.05);
    previousFrame = now;
    if (document.hidden) return;
    if (cameraTransition) {
      const progress = Math.min((now - cameraTransition.start) / 320, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      camera.position.lerpVectors(
        cameraTransition.from,
        cameraTransition.to,
        eased,
      );
      if (progress === 1) cameraTransition = null;
      dirty = true;
    }
    controls.update(delta);
    if (Math.abs(steeringTarget - steering) > 0.0001) {
      steering = THREE.MathUtils.damp(steering, steeringTarget, 12, delta);
      dirty = true;
    }
    for (const pivot of car.frontWheels) pivot.rotation.y = steering;
    if (wheelsSpinning) {
      for (const wheel of car.spinningWheels) wheel.rotation.z -= delta * 1.8;
      dirty = true;
    }
    if (dirty || controls.autoRotate) {
      renderer.render(scene, camera);
      dirty = false;
    }
  }
  renderer.render(scene, camera);
  $("scene-status").hidden = true;
  canvas.setAttribute("aria-busy", "false");
  requestAnimationFrame(frame);
}

function showViewerError(message) {
  $("scene-status").hidden = true;
  $("scene-error").hidden = false;
  const errorMessage = $("scene-error-message");
  if (errorMessage) errorMessage.textContent = message;
  $("scene").setAttribute("aria-busy", "false");
  document
    .querySelectorAll(
      "[data-view], [data-color], #auto-rotate, #reset-view, #steering, #center-steering, #wheel-spin, #headlights, #show-systems, #explode",
    )
    .forEach((control) => {
      control.disabled = true;
    });
}
$("reload-scene").addEventListener("click", () => location.reload());
startViewer().catch((error) => {
  console.error("Car viewer:", error);
  showViewerError(
    "Не удалось загрузить 3D-модель. Проверьте подключение и поддержку WebGL в браузере.",
  );
});
