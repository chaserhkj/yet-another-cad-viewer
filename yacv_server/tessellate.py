import hashlib
import io
import re
from typing import List, Dict, Tuple, Union

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GCPnts import GCPnts_TangentialDeflection
from OCP.TopExp import TopExp
from OCP.TopLoc import TopLoc_Location
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS_Face, TopoDS_Edge, TopoDS_Shape, TopoDS_Vertex
from build123d import Shape, Vertex, Face
from pygltflib import GLTF2

import mylogger
from gltf import GLTFMgr


# TODO: Migrate to ocp-tessellate to reuse the tessellation logic


def tessellate(
        ocp_shape: TopoDS_Shape,
        tolerance: float = 0.1,
        angular_tolerance: float = 0.1,
        faces: bool = True,
        edges: bool = True,
        vertices: bool = True,
) -> GLTF2:
    """Tessellate a whole shape into a list of triangle vertices and a list of triangle indices."""
    mgr = GLTFMgr()
    shape = Shape(ocp_shape)

    # Perform tessellation tasks
    edge_to_faces: Dict[TopoDS_Edge, List[TopoDS_Face]] = {}
    vertex_to_faces: Dict[TopoDS_Vertex, List[TopoDS_Face]] = {}
    if faces:
        for face in shape.faces():
            _tessellate_face(mgr, face.wrapped, tolerance, angular_tolerance)
            if edges:
                for edge in face.edges():
                    edge_to_faces[edge.wrapped] = edge_to_faces.get(edge.wrapped, []) + [face.wrapped]
            if vertices:
                for vertex in face.vertices():
                    vertex_to_faces[vertex.wrapped] = vertex_to_faces.get(vertex.wrapped, []) + [face.wrapped]
    if edges:
        for edge in shape.edges():
            _tessellate_edge(mgr, edge.wrapped, edge_to_faces.get(edge.wrapped, []), angular_tolerance,
                             angular_tolerance)
    if vertices:
        for vertex in shape.vertices():
            _tessellate_vertex(mgr, vertex.wrapped, vertex_to_faces.get(vertex.wrapped, []))

    return mgr.gltf


def _tessellate_face(
        mgr: GLTFMgr,
        ocp_face: TopoDS_Face,
        tolerance: float = 1e-3,
        angular_tolerance: float = 0.1
):
    face = Shape(ocp_face)
    face.mesh(tolerance, angular_tolerance)
    poly = BRep_Tool.Triangulation_s(face.wrapped, TopLoc_Location())
    if poly is None:
        mylogger.logger.warn("No triangulation found for face")
        return GLTF2()
    tri_mesh = face.tessellate(tolerance, angular_tolerance)

    # Get UV of each face from the parameters
    uv = [
        (v.X(), v.Y())
        for v in (poly.UVNode(i) for i in range(1, poly.NbNodes() + 1))
    ]

    vertices = [(v.X, v.Y, v.Z) for v in tri_mesh[0]]
    indices = tri_mesh[1]
    mgr.add_face(vertices, indices, uv)


def _push_point(v: Tuple[float, float, float], faces: List[TopoDS_Face]) -> Tuple[float, float, float]:
    # Use the connected faces to push edges/vertices and make them always visible
    push_dir = (0, 0, 0)
    for ocp_face in faces:
        normal = Face(ocp_face).normal_at(v)
        push_dir = (push_dir[0] + normal.X, push_dir[1] + normal.Y, push_dir[2] + normal.Z)
    if push_dir != (0, 0, 0):
        # Normalize the push direction by the number of faces and a constant factor
        # NOTE: Don't overdo it, or metrics will be wrong
        n = len(faces) / 1e-3
        push_dir = (push_dir[0] / n, push_dir[1] / n, push_dir[2] / n)
        # Push the vertex by the normal
        v = (v[0] + push_dir[0], v[1] + push_dir[1], v[2] + push_dir[2])
    return v


def _tessellate_edge(
        mgr: GLTFMgr,
        ocp_edge: TopoDS_Edge,
        faces: List[TopoDS_Face],
        angular_deflection: float = 0.1,
        curvature_deflection: float = 0.1,
):
    # Use a curve discretizer to get the vertices
    curve = BRepAdaptor_Curve(ocp_edge)
    discretizer = GCPnts_TangentialDeflection(curve, angular_deflection, curvature_deflection)
    assert discretizer.NbPoints() > 1, "Edge is too small??"

    # add vertices
    vertices = [
        _push_point((v.X(), v.Y(), v.Z()), faces)
        for v in (
            discretizer.Value(i)  # .Transformed(transformation)
            for i in range(1, discretizer.NbPoints() + 1)
        )
    ]
    mgr.add_edge(vertices)


def _tessellate_vertex(mgr: GLTFMgr, ocp_vertex: TopoDS_Vertex, faces: List[TopoDS_Face]):
    c = Vertex(ocp_vertex).center()
    mgr.add_vertex(_push_point((c.X, c.Y, c.Z), faces))


def _hashcode(obj: Union[bytes, TopoDS_Shape], **extras) -> str:
    """Utility to compute the hash code of a shape recursively without the need to tessellate it"""
    # NOTE: obj.HashCode(MAX_HASH_CODE) is not stable across different runs of the same program
    # This is best-effort and not guaranteed to be unique
    map_of_shapes = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(obj, map_of_shapes)
    hasher = hashlib.md5(usedforsecurity=False)
    for k, v in extras.items():
        hasher.update(str(k).encode())
        hasher.update(str(v).encode())
    if isinstance(obj, bytes):
        hasher.update(obj)
    else:
        for i in range(1, map_of_shapes.Extent() + 1):
            sub_shape = map_of_shapes.FindKey(i)
            sub_data = io.BytesIO()
            TopoDS_Shape.DumpJson(sub_shape, sub_data)
            val = sub_data.getvalue()
            val = re.sub(b'"this": "[^"]*"', b'', val)  # Remove memory address
            hasher.update(val)
    return hasher.hexdigest()
