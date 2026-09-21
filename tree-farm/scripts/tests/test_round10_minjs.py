# -*- coding: utf-8 -*-
"""
树场 20 轮测试 · 第 10 轮：3DGS 真实项目回归（min.js 误报治理）（unittest）

来源：扫 3DGS viewer 目录（three.min.js + index.html）实测误报——
压缩第三方库一行数万字符，文件级污点收集把 three.min.js:6 误报为
「命令注入 + SSRF（跨行污点）」。

修复（r10，analysis.py）：*.min.js 压缩构建产物跳过安全检测。

运行：
  python3 -m unittest tests.test_round10_minjs -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_project(files: dict) -> str:
    d = tempfile.mkdtemp(prefix="treefarm_r10_")
    for name, content in files.items():
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    return d


class MinJsSkipTest(unittest.TestCase):
    def test_min_js_skipped(self):
        # 压缩库一行超长，含危险函数名 + 疑似污点赋值 → 不应报
        d = make_project({
            "three.min.js": '''!function(t,e){"object"==typeof exports&&"undefined"!=typeof module?e(exports):"function"==typeof define&&define.amd?define(["exports"],e):e((t="undefined"!=typeof globalThis?globalThis:t||self).THREE={})}(this,(function(t){"use strict";var e=t.SVGElement||function(){};var n=function(t,e){var n=e.exec(t);return n?n[0]:null};function i(t){return"string"==typeof t?new Error(t):t}function r(t){t.faces=[];t.vertexColors=[];t.faceVertexUvs=[[]]}t.REVISION="158";t.MOUSE={LEFT:0,MIDDLE:1,RIGHT:2,ROTATE:0,DOLlY:1,PAN:2};t.COLOR_SPACES={};function a(t){if("object"==typeof t)return t.isColor?"Color":t.isMaterial?"Material":t.isVector2?"Vector2":t.isVector3?"Vector3":t.isVector4?"Vector4":t.isEuler?"Euler":t.isQuaternion?"Quaternion":t.isMatrix3?"Matrix3":t.isMatrix4?"Matrix4":t.isSpherical?"Spherical":t.isBox3?"Box3":t.isSphere?"Sphere":t.isPlane?"Plane":t.isFrustum?"Frustum":t.isCamera?"Camera":t.isScene?"Scene":t.isMesh?"Mesh":t.isLine?"Line":t.isPoints?"Points":t.isSprite?"Sprite":t.isBufferGeometry?"BufferGeometry":t.isInstancedBufferGeometry?"InstancedBufferGeometry":t.isInterleavedBuffer?"InterleavedBuffer":t.isInterleavedBufferAttribute?"InterleavedBufferAttribute":t.isBone?"Bone":t.isObject3D?"Object3D":t.isMaterial?"Material":t.isGroup?"Group":t.isLight?"Light":t.isAmbientLight?"AmbientLight":t.isDirectionalLight?"DirectionalLight":t.isPointLight?"PointLight":t.isSpotLight?"SpotLight":t.isHemisphereLight?"HemisphereLight":t.isRectAreaLight?"RectAreaLight":t.isLineSegments?"LineSegments":t.isPoints?"Points":t.isSprite?"Sprite":t.isLOD?"LOD":t.isPerspectiveCamera?"PerspectiveCamera":t.isOrthographicCamera?"OrthographicCamera":t.isAudio?"Audio":t.isVideoTexture?"VideoTexture":t.isDataTexture?"DataTexture":t.isCompressedTexture?"CompressedTexture":t.isCanvasTexture?"CanvasTexture":t.isDepthTexture?"DepthTexture":t.isWebGLRenderTarget?"WebGLRenderTarget":t.isWebGLRenderTargetCube?"WebGLRenderTargetCube":t.isWebGLRenderer?"WebGLRenderer":t.isAudioListener?"AudioListener":t.isAudioContext?"AudioContext":t.isAudio?"Audio":t.isLoader?"Loader":t.isFileLoader?"FileLoader":t.isMaterialLoader?"MaterialLoader":t.isTextureLoader?"TextureLoader":t.isCubeTextureLoader?"CubeTextureLoader":t.isLoadingManager?"LoadingManager":t.isCache?"Cache":t.isBufferGeometryLoader?"BufferGeometryLoader":t.isObjectLoader?"ObjectLoader":t.isDataTextureLoader?"DataTextureLoader":t.isCompressedTextureLoader?"CompressedTextureLoader":t.isTexture?"Texture":t.isFontLoader?"FontLoader":t.isShapeGeometry?"ShapeGeometry":t.isBoxGeometry?"BoxGeometry":t.isCapsuleGeometry?"CapsuleGeometry":t.isCircleGeometry?"CircleGeometry":t.isConeGeometry?"ConeGeometry":t.isCylinderGeometry?"CylinderGeometry":t.isDodecahedronGeometry?"DodecahedronGeometry":t.isEdgesGeometry?"EdgesGeometry":t.isExtrudeGeometry?"ExtrudeGeometry":t.isIcosahedronGeometry?"IcosahedronGeometry":t.isLatheGeometry?"LatheGeometry":t.isOctahedronGeometry?"OctahedronGeometry":t.isPlaneGeometry?"PlaneGeometry":t.isPolyhedronGeometry?"PolyhedronGeometry":t.isRingGeometry?"RingGeometry":t.isShapeGeometry?"ShapeGeometry":t.isSphereGeometry?"SphereGeometry":t.isTetrahedronGeometry?"TetrahedronGeometry":t.isTorusGeometry?"TorusGeometry":t.isTorusKnotGeometry?"TorusKnotGeometry":t.isTubeGeometry?"TubeGeometry":t.isWireframeGeometry?"WireframeGeometry":t.isRawShaderMaterial?"RawShaderMaterial":t.isShaderMaterial?"ShaderMaterial":t.isLineBasicMaterial?"LineBasicMaterial":t.isLineDashedMaterial?"LineDashedMaterial":t.isMeshBasicMaterial?"MeshBasicMaterial":t.isMeshLambertMaterial?"MeshLambertMaterial":t.isMeshPhongMaterial?"MeshPhongMaterial":t.isMeshStandardMaterial?"MeshStandardMaterial":t.isMeshPhysicalMaterial?"MeshPhysicalMaterial":t.isMeshNormalMaterial?"MeshNormalMaterial":t.isMeshToonMaterial?"MeshToonMaterial":t.isMeshDepthMaterial?"MeshDepthMaterial":t.isPointsMaterial?"PointsMaterial":t.isSpriteMaterial?"SpriteMaterial":t.isShadowMaterial?"ShadowMaterial":t.isMaterial?"Material":null}function o(t){return"string"==typeof t?JSON.parse(t):t}function s(t){var e=t.exec?t.exec(t):null;return e?e[0]:null}t.fetch=function(t,e){return fetch(t,e)};t.fetchJson=function(t){return fetch(t).then(o)};t.mesh=function(t,e,n){return new t.Mesh(e,n)};t.loadAsync=function(t,e){return t.loadAsync(t,e)};t.loadTextureAsync=function(t,e){return t.loadTextureAsync(t,e)};}));
''',
        })
        try:
            issues = detect_security_issues([os.path.join(d, "three.min.js")])["issues"]
            self.assertEqual([], issues, f"min.js 误报: {issues}")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_normal_js_still_scanned(self):
        d = make_project({
            "app.js": "function go() {\n    eval(userInput);\n}\n",
        })
        try:
            issues = detect_security_issues([os.path.join(d, "app.js")])["issues"]
            self.assertTrue(len(issues) >= 1, "普通 JS 仍应检测")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
