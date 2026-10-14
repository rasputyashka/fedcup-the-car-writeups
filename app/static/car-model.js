// Procedural VAZ-2106. Front is +X; wheel axles are local Z.
// The model never translates: callers may steer frontWheels and spin spinningWheels.
export function createCar(THREE) {
  const group = new THREE.Group();
  group.name = 'VAZ-2106';
  const frontWheels = [];
  const spinningWheels = [];
  const paint = new THREE.MeshPhysicalMaterial({ color: 0xdfd6c0, metalness: 0.22, roughness: 0.3, clearcoat: 0.85, clearcoatRoughness: 0.2, side: THREE.DoubleSide });
  const chrome = new THREE.MeshStandardMaterial({ color: 0xdce0e2, metalness: 0.94, roughness: 0.21 });
  const steel = new THREE.MeshStandardMaterial({ color: 0xa8aaa4, metalness: 0.72, roughness: 0.4 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x17191a, roughness: 0.78 });
  const seam = new THREE.MeshStandardMaterial({ color: 0x655f52, roughness: 0.8 });
  const tire = new THREE.MeshStandardMaterial({ color: 0x171819, roughness: 0.94 });
  const tread = new THREE.MeshStandardMaterial({ color: 0x222425, roughness: 1 });
  const glass = new THREE.MeshPhysicalMaterial({ color: 0x617474, metalness: 0.12, roughness: 0.13, transparent: true, opacity: 0.58, side: THREE.DoubleSide, depthWrite: false, clearcoat: 1 });
  const upholstery = new THREE.MeshStandardMaterial({ color: 0x302c27, roughness: 0.93 });
  const headlight = new THREE.MeshStandardMaterial({ color: 0xe3e0d0, emissive: 0xffe3a8, emissiveIntensity: 0.08, metalness: 0.1, roughness: 0.22 });
  const lensRib = new THREE.MeshStandardMaterial({ color: 0xc6c8bd, metalness: 0.35, roughness: 0.25 });
  const red = new THREE.MeshStandardMaterial({ color: 0xa21f18, emissive: 0xb52612, emissiveIntensity: 0.05, roughness: 0.3 });
  const amber = new THREE.MeshStandardMaterial({ color: 0xd98623, roughness: 0.3 });
  const white = new THREE.MeshStandardMaterial({ color: 0xd8d4ba, roughness: 0.3 });

  function mesh(geometry, material, position = [0, 0, 0], rotation = [0, 0, 0], parent = group) {
    const object = new THREE.Mesh(geometry, material);
    object.position.set(...position);
    object.rotation.set(...rotation);
    object.castShadow = material !== glass;
    object.receiveShadow = true;
    parent.add(object);
    return object;
  }
  function box(size, material, position, rotation, parent) {
    return mesh(new THREE.BoxGeometry(...size), material, position, rotation, parent);
  }
  function roundBox(size, radius = 0.02) {
    const [width, height, depth] = size;
    const r = Math.min(radius, width / 4, height / 4, depth / 4);
    const x = -width / 2 + r, y = -height / 2 + r;
    const w = width - 2 * r, h = height - 2 * r;
    const shape = new THREE.Shape();
    shape.moveTo(x, y); shape.lineTo(x + w, y); shape.lineTo(x + w, y + h);
    shape.lineTo(x, y + h); shape.closePath();
    const geometry = new THREE.ExtrudeGeometry(shape, { depth: depth - 2 * r, bevelEnabled: true, bevelThickness: r, bevelSize: r, bevelSegments: 2, steps: 1 });
    geometry.translate(0, 0, -depth / 2 + r);
    return geometry;
  }
  function rod(points, material, radius = 0.008, parent = group) {
    const vectors = points.map(p => new THREE.Vector3(...p));
    const path = new THREE.CurvePath();
    for (let i = 1; i < vectors.length; i++) path.add(new THREE.LineCurve3(vectors[i - 1], vectors[i]));
    return mesh(new THREE.TubeGeometry(path, Math.max(2, points.length * 3), radius, 6, false), material, undefined, undefined, parent);
  }
  function panel(points, material) {
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(points.flat());
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const indices = [];
    for (let i = 1; i < points.length - 1; i++) indices.push(0, i, i + 1);
    geometry.setIndex(indices); geometry.computeVertexNormals();
    return mesh(geometry, material);
  }
  function glazing(points) {
    const perimeter = [...points, points[0]];
    rod(perimeter, dark, 0.018);
    rod(perimeter, chrome, 0.007);
    return panel(points, glass);
  }

  // Thin side panels leave the cabin hollow and the wheel arches open.
  // The straight belt line and short overhangs retain the classic six's shape.
  const wheelY = 0.333, wheelRadius = 0.329;
  const axles = [-1.23, 1.24];
  const archRadius = 0.415, sillY = 0.325;
  const archAngle = Math.asin((sillY - wheelY) / archRadius);
  const body = new THREE.Shape();
  body.moveTo(-2.04, sillY);
  for (const axle of axles) {
    body.lineTo(axle - Math.cos(archAngle) * archRadius, sillY);
    body.absarc(axle, wheelY, archRadius, Math.PI - archAngle, archAngle, true);
  }
  body.lineTo(2.04, sillY); body.lineTo(2.06, 0.82);
  body.quadraticCurveTo(2.06, 0.92, 1.99, 0.937);
  body.lineTo(0.83, 0.974); body.lineTo(-1.2, 0.974);
  body.lineTo(-1.98, 0.941); body.quadraticCurveTo(-2.065, 0.93, -2.065, 0.865);
  body.lineTo(-2.04, sillY); body.closePath();
  for (const side of [-1, 1]) {
    const bodyGeometry = new THREE.ExtrudeGeometry(body, { depth: 0.045, bevelEnabled: true, bevelThickness: 0.025, bevelSize: 0.015, bevelSegments: 2, curveSegments: 22, steps: 1 });
    bodyGeometry.translate(0, 0, side > 0 ? 0.73 : -0.775);
    mesh(bodyGeometry, paint);
  }
  // Separate bonnet and boot decks keep bodywork out of the passenger cabin.
  mesh(roundBox([1.2, 0.12, 1.52], 0.018), paint, [1.46, 0.901, 0], [0, 0, -0.031]);
  mesh(roundBox([0.85, 0.12, 1.52], 0.018), paint, [-1.635, 0.904, 0], [0, 0, 0.039]);
  box([0.07, 0.41, 1.53], paint, [2.025, 0.66, 0]);
  box([0.07, 0.41, 1.53], paint, [-2.025, 0.66, 0]);
  box([3.5, 0.11, 1.12], dark, [0, 0.295, 0]);

  // Folded lips follow the arch openings in the vertical plane.
  for (const side of [-1, 1]) {
    for (const axle of axles) {
      const arch = [];
      for (let i = 0; i <= 36; i++) {
        const angle = Math.PI * i / 36;
        arch.push([axle + Math.cos(angle) * (archRadius + 0.008), wheelY + Math.sin(angle) * (archRadius + 0.008), side * 0.805]);
      }
      rod(arch, paint, 0.019);
      const inner = arch.map(p => [p[0], p[1] - 0.014, side * 0.777]);
      rod(inner, dark, 0.012);
    }
    box([1.55, 0.07, 0.028], paint, [-0.005, 0.34, side * 0.802]);
    rod([[-0.79, 0.324, side * 0.819], [0.8, 0.324, side * 0.819]], chrome, 0.009);
  }

  // Bonnet and boot seams are inset, rather than extra floating slabs.
  for (const side of [-1, 1]) {
    rod([[0.87, 0.979, side * 0.664], [1.91, 0.949, side * 0.664]], seam, 0.004);
    rod([[-1.21, 0.979, side * 0.675], [-1.96, 0.948, side * 0.675]], seam, 0.004);
  }
  rod([[1.92, 0.95, -0.663], [1.92, 0.95, 0.663]], seam, 0.004);
  rod([[-1.96, 0.949, -0.675], [-1.96, 0.949, 0.675]], seam, 0.004);
  // Cowl ventilation slots at the base of the windshield.
  for (const side of [-1, 1]) {
    for (let i = 0; i < 9; i++) box([0.07, 0.007, 0.014], dark, [0.915, 0.984, side * (0.17 + i * 0.038)]);
  }

  // A real open cabin: sloping glass, thin pillars, roof and visible interior.
  box([2.32, 0.07, 1.44], upholstery, [-0.12, 0.65, 0]);
  for (const side of [-1, 1]) box([2.04, 0.25, 0.03], upholstery, [-0.12, 0.825, side * 0.718]);
  mesh(roundBox([1.36, 0.068, 1.36], 0.024), paint, [-0.16, 1.455, 0]);
  box([1.28, 0.018, 1.24], upholstery, [-0.16, 1.414, 0]);
  const windshield = [[0.91, 1.015, -0.714], [0.91, 1.015, 0.714], [0.462, 1.411, 0.645], [0.462, 1.411, -0.645]];
  const backlight = [[-1.232, 1.014, 0.715], [-1.232, 1.014, -0.715], [-0.796, 1.411, -0.645], [-0.796, 1.411, 0.645]];
  glazing(windshield); glazing(backlight);
  for (const side of [-1, 1]) {
    const zAt = y => side * (0.744 - (y - 0.997) * 0.222);
    const sidePoints = points => points.map(([x, y]) => [x, y, zAt(y)]);
    const frontWindow = sidePoints([[0.83, 1.015], [-0.11, 1.015], [-0.11, 1.409], [0.424, 1.409]]);
    const rearWindow = sidePoints([[-0.204, 1.015], [-1.105, 1.015], [-0.754, 1.409], [-0.204, 1.409]]);
    glazing(frontWindow); glazing(rearWindow);
    rod([[0.924, 0.986, side * 0.755], [0.48, 1.435, side * 0.663]], paint, 0.028);
    rod([[-1.258, 0.983, side * 0.754], [-0.806, 1.435, side * 0.663]], paint, 0.04);
    // Painted B pillars; a narrow chrome edge and front quarter-light divider.
    panel(sidePoints([[-0.2, 0.988], [-0.112, 0.988], [-0.112, 1.437], [-0.2, 1.437]]), paint);
    rod([[-0.157, 1.003, side * 0.751], [-0.157, 1.42, side * 0.662]], chrome, 0.008);
    rod([[0.546, 1.013, zAt(1.013)], [0.319, 1.405, zAt(1.405)]], chrome, 0.009);
    rod([[-1.25, 0.999, side * 0.757], [0.93, 0.999, side * 0.757]], chrome, 0.012);
    rod([[-0.832, 1.457, side * 0.682], [0.514, 1.457, side * 0.682]], chrome, 0.009);
    // Door outlines follow the sills and end before the wheel openings.
    const surface = side * 0.804;
    rod([[0.878, 0.957, surface], [0.845, 0.79, surface], [0.79, 0.715, surface], [0.777, 0.409, surface], [-0.12, 0.409, surface], [-0.14, 0.953, surface]], seam, 0.0045);
    rod([[-0.153, 0.409, surface], [-0.784, 0.409, surface], [-0.798, 0.73, surface], [-1.13, 0.95, surface]], seam, 0.0045);
    // Thin continuous chrome side strip sits above the wheel cutouts.
    rod([[-1.995, 0.804, side * 0.808], [2.006, 0.804, side * 0.808]], chrome, 0.011);
    for (const x of [0.025, -0.885]) {
      mesh(roundBox([0.165, 0.034, 0.025], 0.006), dark, [x, 0.901, side * 0.807]);
      mesh(roundBox([0.15, 0.022, 0.038], 0.005), chrome, [x, 0.907, side * 0.824]);
      box([0.013, 0.022, 0.004], dark, [x + 0.045, 0.867, side * 0.819]);
    }
    // Small amber fender repeater, square metal mirrors on short stems.
    mesh(roundBox([0.085, 0.04, 0.015], 0.004), chrome, [1.69, 0.815, side * 0.813]);
    box([0.065, 0.027, 0.018], amber, [1.69, 0.815, side * 0.823]);
    rod([[0.73, 0.989, side * 0.742], [0.7, 1.052, side * 0.912]], chrome, 0.014);
    mesh(roundBox([0.055, 0.126, 0.183], 0.018), chrome, [0.707, 1.072, side * 0.924], [0, side * -0.13, 0]);
    mesh(roundBox([0.006, 0.103, 0.15], 0.002), glass, [0.675, 1.072, side * 0.924], [0, side * -0.13, 0]);
  }

  // Simple recognizable cabin details, seen through all four door windows.
  mesh(roundBox([0.26, 0.12, 1.32], 0.025), dark, [0.633, 0.994, 0]);
  for (const side of [-1, 1]) {
    mesh(roundBox([0.51, 0.135, 0.5], 0.04), upholstery, [0.0, 0.805, side * 0.377]);
    mesh(roundBox([0.12, 0.48, 0.49], 0.04), upholstery, [-0.24, 1.018, side * 0.377], [0, 0, -0.12]);
    for (let i = -2; i <= 2; i++) rod([[-0.167, 0.872, side * 0.377 + i * 0.074], [-0.195, 1.195, side * 0.377 + i * 0.074]], dark, 0.004);
  }
  mesh(roundBox([0.44, 0.13, 1.19], 0.04), upholstery, [-0.77, 0.825, 0]);
  mesh(roundBox([0.105, 0.39, 1.18], 0.035), upholstery, [-1.014, 1.035, 0], [0, 0, -0.16]);
  const steeringWheel = new THREE.Group();
  steeringWheel.position.set(0.42, 1.023, 0.38); steeringWheel.rotation.z = 0.43; group.add(steeringWheel);
  mesh(new THREE.TorusGeometry(0.145, 0.014, 8, 32), dark, undefined, [0, Math.PI / 2, 0], steeringWheel);
  box([0.028, 0.024, 0.25], dark, [0, 0, 0], undefined, steeringWheel);
  box([0.03, 0.115, 0.023], dark, [0, -0.053, 0], undefined, steeringWheel);
  for (const z of [-0.35, 0.28]) rod([[0.878, 1.035, z - 0.12], [0.69, 1.181, z + 0.1], [0.589, 1.27, z + 0.13]], dark, 0.008);

  // Quad round headlights, a narrow black grille and chrome bezels.
  mesh(roundBox([0.058, 0.272, 1.546], 0.015), dark, [2.065, 0.751, 0]);
  for (const side of [-1, 1]) {
    rod([[2.102, 0.614, side * 0.323], [2.102, 0.614, side * 0.779], [2.102, 0.89, side * 0.779], [2.102, 0.89, side * 0.323], [2.102, 0.614, side * 0.323]], chrome, 0.007);
    for (const offset of [0.431, 0.665]) {
      const z = side * offset;
      mesh(new THREE.CylinderGeometry(0.112, 0.112, 0.028, 36), headlight, [2.109, 0.753, z], [0, 0, Math.PI / 2]);
      mesh(new THREE.TorusGeometry(0.117, 0.011, 8, 36), chrome, [2.13, 0.753, z], [0, Math.PI / 2, 0]);
      // Lens flutes make the lamps read as old glass, even with lights off.
      for (let i = -3; i <= 3; i++) {
        const offsetZ = i * 0.025;
        const height = Math.sqrt(0.102 ** 2 - offsetZ ** 2) * 2;
        box([0.003, height, 0.003], lensRib, [2.125, 0.753, z + offsetZ]);
      }
    }
    mesh(roundBox([0.04, 0.09, 0.263], 0.01), chrome, [2.074, 0.557, side * 0.574]);
    box([0.045, 0.063, 0.12], amber, [2.081, 0.557, side * 0.635]);
    box([0.045, 0.063, 0.109], white, [2.081, 0.557, side * 0.508]);
  }
  for (let i = 0; i < 7; i++) box([0.02, 0.007, 0.608], chrome, [2.102, 0.658 + i * 0.032, 0]);
  for (const z of [-0.306, 0.306]) box([0.025, 0.231, 0.009], chrome, [2.102, 0.754, z]);
  mesh(roundBox([0.032, 0.09, 0.043], 0.007), chrome, [2.127, 0.763, 0]);
  box([0.006, 0.062, 0.025], red, [2.147, 0.763, 0]);

  // Rectangular rear clusters with separate amber, red and reversing lenses.
  for (const side of [-1, 1]) {
    mesh(roundBox([0.05, 0.183, 0.484], 0.015), chrome, [-2.077, 0.765, side * 0.51]);
    box([0.056, 0.151, 0.455], dark, [-2.09, 0.765, side * 0.51]);
    for (const [offset, material, width] of [[0.674, amber, 0.112], [0.527, red, 0.17], [0.367, white, 0.111]]) {
      box([0.06, 0.122, width], material, [-2.096, 0.765, side * offset]);
      for (const dy of [-0.027, 0.024]) box([0.002, 0.005, width], chrome, [-2.129, 0.765 + dy, side * offset]);
    }
  }

  // Chrome bumpers have restrained black rubbing strips and upright guards.
  for (const direction of [-1, 1]) {
    const x = direction * 2.161;
    mesh(roundBox([0.136, 0.12, 1.735], 0.035), chrome, [x, 0.439, 0]);
    mesh(roundBox([0.018, 0.043, 1.64], 0.005), dark, [x + direction * 0.071, 0.439, 0]);
    for (const side of [-1, 1]) {
      mesh(roundBox([0.2, 0.113, 0.092], 0.02), chrome, [x - direction * 0.076, 0.44, side * 0.82]);
      mesh(roundBox([0.135, 0.225, 0.077], 0.012), dark, [x + direction * 0.024, 0.47, side * 0.402]);
      box([0.017, 0.192, 0.014], chrome, [x + direction * 0.093, 0.47, side * 0.442]);
    }
  }
  const plateCanvas = document.createElement('canvas');
  plateCanvas.width = 512; plateCanvas.height = 128;
  const plateContext = plateCanvas.getContext('2d');
  plateContext.fillStyle = '#e9e6db'; plateContext.fillRect(0, 0, 512, 128);
  plateContext.strokeStyle = '#30312e'; plateContext.lineWidth = 5; plateContext.strokeRect(5, 5, 502, 118);
  plateContext.fillStyle = '#222522'; plateContext.font = 'bold 76px Arial';
  plateContext.textAlign = 'center'; plateContext.textBaseline = 'middle'; plateContext.fillText('ВАЗ 2106', 256, 68);
  const plateTexture = new THREE.CanvasTexture(plateCanvas); plateTexture.colorSpace = THREE.SRGBColorSpace;
  const plateMaterial = new THREE.MeshStandardMaterial({ map: plateTexture, roughness: 0.72 });
  for (const direction of [-1, 1]) {
    box([0.026, 0.14, 0.556], dark, [direction * 2.081, 0.549, 0]);
    mesh(new THREE.PlaneGeometry(0.53, 0.126), plateMaterial, [direction * 2.099, 0.549, 0], [0, direction * Math.PI / 2, 0]);
  }
  mesh(new THREE.CylinderGeometry(0.037, 0.037, 0.28, 16, 1, true), chrome, [-2.08, 0.277, -0.575], [0, 0, Math.PI / 2]);
  mesh(new THREE.CircleGeometry(0.029, 16), dark, [-2.224, 0.277, -0.575], [0, -Math.PI / 2, 0]);
  for (const side of [-1, 1]) box([0.025, 0.15, 0.195], dark, [-1.61, 0.263, side * 0.752]);
  // Flush filler flap and small boot badge.
  rod([[-1.677, 0.863, -0.808], [-1.503, 0.863, -0.808], [-1.503, 0.963, -0.808], [-1.677, 0.956, -0.808], [-1.677, 0.863, -0.808]], seam, 0.004);
  box([0.013, 0.027, 0.175], chrome, [-2.084, 0.895, -0.538]);

  // Radial tire profile, tread blocks, vented steel wheels and domed hubcaps.
  const tireProfile = [
    [0.196, -0.082], [0.22, -0.104], [0.279, -0.108], [0.313, -0.08],
    [wheelRadius, -0.043], [wheelRadius, 0.043], [0.313, 0.08],
    [0.279, 0.108], [0.22, 0.104], [0.196, 0.082], [0.196, -0.082],
  ].map(([radius, depth]) => new THREE.Vector2(radius, depth));
  const tireGeometry = new THREE.LatheGeometry(tireProfile, 48);
  const treadGeometry = new THREE.BoxGeometry(0.016, 0.004, 0.084);
  function wheel() {
    const spin = new THREE.Group();
    mesh(tireGeometry, tire, undefined, [Math.PI / 2, 0, 0], spin);
    const blocks = new THREE.InstancedMesh(treadGeometry, tread, 80);
    const transform = new THREE.Object3D();
    for (let i = 0; i < 40; i++) {
      const angle = i / 40 * Math.PI * 2;
      for (let j = 0; j < 2; j++) {
        transform.position.set(-Math.sin(angle) * wheelRadius, Math.cos(angle) * wheelRadius, j ? 0.042 : -0.042);
        transform.rotation.set(0, j ? 0.32 : -0.32, angle);
        transform.updateMatrix(); blocks.setMatrixAt(i * 2 + j, transform.matrix);
      }
    }
    blocks.castShadow = true; spin.add(blocks);
    mesh(new THREE.CylinderGeometry(0.21, 0.21, 0.178, 40), steel, undefined, [Math.PI / 2, 0, 0], spin);
    for (const side of [-1, 1]) {
      mesh(new THREE.TorusGeometry(0.207, 0.01, 8, 40), chrome, [0, 0, side * 0.093], undefined, spin);
      mesh(new THREE.TorusGeometry(0.291, 0.0025, 6, 48), tread, [0, 0, side * 0.106], undefined, spin);
      for (let i = 0; i < 10; i++) {
        const angle = i / 10 * Math.PI * 2;
        const vent = mesh(new THREE.CircleGeometry(0.023, 12), dark, [Math.sin(angle) * 0.17, Math.cos(angle) * 0.17, side * 0.0905], [0, side < 0 ? Math.PI : 0, 0], spin);
        vent.scale.x = 0.66;
      }
      const cap = mesh(new THREE.SphereGeometry(0.139, 28, 12), chrome, [0, 0, side * 0.099], undefined, spin);
      cap.scale.z = 0.24;
      mesh(new THREE.TorusGeometry(0.14, 0.005, 8, 32), chrome, [0, 0, side * 0.104], undefined, spin);
      mesh(new THREE.CircleGeometry(0.022, 16), steel, [0, 0, side * 0.135], [0, side < 0 ? Math.PI : 0, 0], spin);
      box([0.011, 0.03, 0.009], dark, [0.156, -0.112, side * 0.108], [0, 0, 0.7], spin);
    }
    return spin;
  }
  for (const axle of axles) {
    for (const side of [-1, 1]) {
      const pivot = new THREE.Group();
      pivot.name = `${axle > 0 ? 'front' : 'rear'}-wheel-${side > 0 ? 'left' : 'right'}`;
      pivot.position.set(axle, wheelY, side * 0.763);
      const spin = wheel();
      spin.name = 'wheel-spin';
      pivot.add(spin); group.add(pivot);
      spinningWheels.push(spin);
      if (axle > 0) frontWheels.push(pivot);
    }
  }

  return {
    group,
    frontWheels,
    spinningWheels,
    setHeadlights(enabled) {
      headlight.emissiveIntensity = enabled ? 1.65 : 0.08;
      red.emissiveIntensity = enabled ? 0.4 : 0.05;
      lensRib.color.setHex(enabled ? 0xe5d9b7 : 0xc6c8bd);
    },
    setColor(hex) { paint.color.set(hex); },
  };
}
