import inspect
import bpy
import bmesh
import os
import math
from math import *
import mathutils
from mathutils import Vector
import shutil
import json

#render engine custom begin
class RendererRenderEngine(bpy.types.RenderEngine):
    bl_idname = 'Renderer_Renderer'
    bl_label = 'Renderer_Renderer'
    bl_use_preview = False
    bl_use_material = True
    bl_use_shading_nodes = False
    bl_use_shading_nodes_custom = False
    bl_use_texture_preview = True
    bl_use_texture = True
    

    def render(self, scene):
        self.report({'ERROR'}, "Use export function in Renderer panel.")
        
from bl_ui import properties_render
from bl_ui import properties_material

for member in dir(properties_render):
    subclass = getattr(properties_render, member)
    try:
        subclass.COMPAT_ENGINES.add('Renderer_Renderer')
    except:
        pass

for member in dir(properties_material):
    subclass = getattr(properties_material, member)
    try:
        subclass.COMPAT_ENGINES.add('Renderer_Renderer')
    except:
        pass

bpy.utils.register_class(RendererRenderEngine)
exportedMaterials = list()

#Camera code:
#https://blender.stackexchange.com/questions/16472/how-can-i-get-the-cameras-projection-matrix
def measure(first, second):
    locx = second[0] - first[0]
    locy = second[1] - first[1]
    locz = second[2] - first[2]

    distance = sqrt((locx)**2 + (locy)**2 + (locz)**2)
    return distance

def export_camera(parent, scene):
    parent.report({'INFO'}, " Fetching camera..")
    cam_ob = bpy.context.scene.camera
    if cam_ob is None:
        parent.error({"ERROR"}, "no scene camera,aborting")
        parent.error_or_warning = True
        parent.fatal_error = True
        return {}
    elif cam_ob.type == 'CAMERA':
        parent.report({'INFO'}, f"Exporting camera: {cam_ob.name} [{scene.render.resolution_x}x{scene.render.resolution_y}]")

        cameramatrix = cam_ob.matrix_world.copy()
        matrixTransposed = cameramatrix.transposed()
        up_point = matrixTransposed[1]

        from_point=cam_ob.matrix_world.col[3]
        at_point=cam_ob.matrix_world.col[2]
        at_point=at_point * -1
        at_point=at_point + from_point

        camera_dict = {}
        
        camera_dict["resolution"] = [
            scene.resolution_x,
            scene.resolution_y
        ]

        # https://blender.stackexchange.com/questions/14745/how-do-i-change-the-focal-length-of-a-camera-with-python
        fov = bpy.data.cameras[0].angle * 180 / math.pi
        camera_dict["vfov"] = fov * (scene.resolution_y/scene.resolution_x)
        
        if scene.dofLookAt is not None:
            camera_dict["fdist"] = measure(cam_ob.matrix_world.translation, scene.dofLookAt.matrix_world.translation)
            camera_dict["aperture"] = scene.lensradius
        else:
            camera_dict["fdist"] = 1.0

        camera_dict["transform"] = {
            "from" : [from_point.x, from_point.y, from_point.z],
            "at" : [at_point.x, at_point.y, at_point.z],
            "up" : [up_point[0],up_point[1],up_point[2]]
        }

        return (camera_dict, {
            "type" : "independent",
            "samples" : scene.spp
        })
        
def texture_might_exist(inputSlot):
    return len(inputSlot.links) > 0

def texture_copy(parent, node, filepath):
    fromFile = bpy.path.abspath(node.image.filepath)
    head, tail = os.path.split(fromFile)
    
    toFile = bpy.path.abspath(filepath + '/textures/' + tail)
    parent.report({'INFO'}, f"Copying texture: {fromFile} to {toFile}")
    if not os.path.exists(os.path.dirname(toFile)):
        os.makedirs(os.path.dirname(toFile))
    
    if not os.path.exists(fromFile):
        parent.report({'ERROR'}, f"Texture does not exist: {fromFile}. You might forget to unpack the file.")
        parent.error_or_warning = True
        parent.fatal_error = True
        return
    
    if os.path.realpath(fromFile) != os.path.realpath(toFile):
        if os.path.exists(toFile):
             parent.report({'WARNING'}, f"Texture already exist: {toFile}. Do not copy")
        else:
            shutil.copyfile(os.path.realpath(fromFile), os.path.realpath(toFile))

def only_value (parent, inputSlot):
    """Return value"""
    if(len(inputSlot.links) == 0):
        if type(inputSlot.default_value) == float:
            return inputSlot.default_value
        else: 
            return [inputSlot.default_value[0], 
                    inputSlot.default_value[1], 
                    inputSlot.default_value[2]
                    ]
  
    node = inputSlot.links[0].from_node # Take always the first link
              
    if node.bl_idname == "ShaderNodeRGB":
        return [
            node.outputs[0].default_value[0],
            node.outputs[0].default_value[1],
            node.outputs[0].default_value[2]
        ]
    
    parent.report({'WARNING'}, f"Texture are not supported for envmap: {node.bl_idname}.")
    parent.error_or_warning = True
    return [0.0, 0.0, 0.0]

def texture_or_value (parent, inputSlot, filepath, scale=1.0, is_normal_map = False):
    """Return BSDF information"""
    if(len(inputSlot.links) == 0):
        if type(inputSlot.default_value) == float:
            return inputSlot.default_value * scale
        else: 
            return [inputSlot.default_value[0] * scale, 
                    inputSlot.default_value[1] * scale, 
                    inputSlot.default_value[2] * scale
                    ]
    
    node = inputSlot.links[0].from_node # Take always the first link

    # Check if normal map, and get the node
    if node.bl_idname == "ShaderNodeNormalMap":
        parent.report({'WARNING'}, f"Detect normal map, Strength is ignored: {node.inputs['Strength'].default_value}")
        if len(node.inputs["Color"].links) > 0:
            node = node.inputs["Color"].links[0].from_node
        else:
            return {}
        

    # Texture type: ShaderNodeTexVoronoi
    parent.report({'INFO'}, f"Texture type: {node.bl_idname}")
    if node.bl_idname == "ShaderNodeTexChecker":
        parent.report({'INFO'}, " Detect checkerboard texture")
        # Checker board
        c1 = node.inputs[1].default_value
        c2 = node.inputs[2].default_value
        uv_scale = node.inputs[3].default_value
        return {
            "color1" : [c1[0], c1[1], c1[2]],
            "color2" : [c2[0], c2[1], c2[2]],
            "uv_scale" : [uv_scale / 2, uv_scale / 2],
            "scale" : scale,
            "type" : "checkerboard2d"
        }
    elif node.bl_idname == "ShaderNodeTexEnvironment":
        texture_copy(parent, node, filepath)
        return {
            "type" : "texture",
            "filename" : "textures/"+node.image.name,
        }
    elif node.bl_idname == "ShaderNodeTexImage":
        filename = node.image.name # os.path.split(node.image.filepath.replace("\\","//"))[-1]
        parent.report({'INFO'}, f"Detect Texture image: {node.image.name} | {filename}") 
        texture_copy(parent, node, filepath)
        if len(node.inputs[0].links) > 0:
            parent.report({'INFO'}, f"Number links For Texture mapping: {len(node.inputs[0].links)}")

            # TODO: Assume mapping node for texture manipulation
            # TODO: Fix this later

            # nodeMapping = node.inputs[0].links[0].from_node
            # scale = nodeMapping.inputs[3].default_value
            # t = nodeMapping.inputs[1].default_value
            # r = nodeMapping.inputs[2].default_value #  order='XYZ'
            # translate = [t[0],t[1],t[2]]
            # scaleXYZ = [scale[0],scale[1],scale[2]]
            # rot_angles = [r[0],r[1],r[2]]
            # rot_anglesDegree = [math.degrees(r[0]) ,math.degrees(r[1]),math.degrees(r[2])]
            
            # TODO: Might not be standard mapping
            return {
                "type" : "texture",
                "filename" : "textures/"+filename,
                # "scale" : scaleXYZ,
                # "translate" : translate,
                # "rotation" : rot_angles,
                # "rotationDegree" : rot_anglesDegree,
                "gamma" : not is_normal_map,
                "scale" : scale
            }
        else:
            # Default export
            return {
                "type" : "texture",
                "filename" : "textures/"+filename,
                "gamma" : not is_normal_map,
                "scale" : scale
            }
    else:
        parent.report({'WARNING'}, f"Unsupported node export: {node.bl_idname} | default: {inputSlot.default_value} | {type(inputSlot.default_value)}")
        parent.error_or_warning = True
        if is_normal_map:
            return {
                "type" : "constant",
                "value" : [0, 0, 1]
            }
        else:
            if type(inputSlot.default_value) == float:
                return inputSlot.default_value * scale
            else: 
                return [inputSlot.default_value[0] * scale, 
                        inputSlot.default_value[1] * scale, 
                        inputSlot.default_value[2] * scale, 
                        ]
            

def export_material_node(parent, scene, mat, rootMaterial, filepath):
    parent.report({'INFO'}, "Exporting material node type : " + mat.bl_idname)
    mat_data = {}

    if "portal_id" in rootMaterial:
        mat_data["type"] = "portal"
        mat_data["portal_id"] = rootMaterial["portal_id"]
        mat_data["scene_id"] = rootMaterial["scene_id"]
    elif mat.bl_idname == 'ShaderNodeBsdfTransparent':
        mat_data["type"] = "transparent"
        mat_data["albedo"] = texture_or_value(parent, mat.inputs[0], filepath)
        mat_data["alpha"] =  mat.inputs[0].default_value[3]
    elif mat.bl_idname == 'ShaderNodeBsdfDiffuse':
        mat_data["type"] = "diffuse"
        mat_data["albedo"] = texture_or_value(parent, mat.inputs[0], filepath)
        mat_data["alpha"] =  mat.inputs[0].default_value[3]
        mat_data["roughness"] = texture_or_value(parent, mat.inputs[1], filepath)
        if texture_might_exist(mat.inputs["Normal"]):
            normal_node = mat.inputs["Normal"].links[0].from_node
            parent.report({'INFO'}, "Normal type : " + normal_node.bl_idname)
            # bump map if bump node (diffuse.normal -> bump.height -> texture)
            if normal_node.bl_idname == "ShaderNodeBump":
                bump_map_params = texture_or_value(parent, normal_node.inputs["Height"], filepath, is_normal_map = True)
                if len(bump_map_params) != 0:
                    parent.report({'INFO'}, "Add bump map!")
                    mat_data["bump_map"] = bump_map_params
            # normal map otherwise
            else:
                normal_map_params = texture_or_value(parent, mat.inputs["Normal"], filepath, is_normal_map = True)
                if len(normal_map_params) != 0:
                    # Normal map added if found
                    parent.report({'INFO'}, "Add normal map!")
                    mat_data["normal_map"] = normal_map_params
                    exported_normal = True
    elif mat.bl_idname == "ShaderNodeEmission":
        mat_data["type"] = "diffuse_light"
        scale = mat.inputs[1].default_value
        mat_data["radiance"] = texture_or_value(parent, mat.inputs[0], filepath, scale)
    elif mat.bl_idname == "ShaderNodeMixShader":
        fresnel = False
        if(len(mat.inputs[0].links) != 0):
            node = mat.inputs[0].links[0].from_node
            fresnel = (node.bl_idname == "ShaderNodeFresnel")
        # else blend
        if fresnel:
            mat_data["type"] = "fresnel_blend"
            fresnel_node = mat.inputs[0].links[0].from_node
            mat_data["eta"] = texture_or_value(parent, fresnel_node.inputs[0], filepath)
        else:
            mat_data["type"] = "blend"
            mat_data["alpha"] = texture_or_value(parent, mat.inputs[0], filepath)
        # parent.report({'INFO'}, "blend 1 : " + mat.bl_idname + ", " + rootMaterial.name)
        mat_data["matA"] = export_material_node(parent, scene, mat.inputs[2].links[0].from_node, rootMaterial, filepath)
        # parent.report({'INFO'}, "blend 2 : " + mat.bl_idname + ", " + rootMaterial.name)
        mat_data["matB"] = export_material_node(parent, scene, mat.inputs[1].links[0].from_node, rootMaterial, filepath)
    elif mat.bl_idname == "ShaderNodeBsdfGlass":
        mat_data["type"] = "dielectric"
        mat_data["ks"] = texture_or_value(parent, mat.inputs[0], filepath) # Color
        mat_data["roughness"] = mat.inputs[1].default_value     # Roughness
        mat_data["eta_int"] = mat.inputs[2].default_value       # IOR
        # TODO: Export IOR (texture - 1d)
    elif mat.bl_idname == "ShaderNodeBsdfGlossy" or mat.bl_idname == "ShaderNodeBsdfAnisotropic":
        mat_data["type"] = "metal"
        mat_data["ks"] = texture_or_value(parent, mat.inputs[0], filepath)
        mat_data["roughness"] = mat.inputs[1].default_value # roughness
    elif mat.bl_idname == "ShaderNodeBsdfPrincipled":
        parent.report({'WARNING'}, " Principled shader not fully supported")
        parent.error_or_warning = True

        # mat_data = {
        #     "type" : "diffuse",
        #     "albedo": texture_or_value(parent, mat.inputs["Base Color"], filepath),
        #     "roughness" : texture_or_value(parent, mat.inputs["Roughness"], filepath),
        #     "specular": texture_or_value(parent, mat.inputs["Specular"], filepath),
        #     "metallic":  texture_or_value(parent, mat.inputs["Metallic"], filepath),
        #     "anisotropic": texture_or_value(parent, mat.inputs["Anisotropic"], filepath),
        #     "translucency":  texture_or_value(parent, mat.inputs["Transmission"], filepath),
        #     "alpha":  texture_or_value(parent, mat.inputs["Alpha"], filepath),
        # }
        # if texture_might_exist(mat.inputs["Normal"]):
        #     normal_node = mat.inputs["Normal"].links[0].from_node
        #     parent.report({'INFO'}, "Normal type : " + normal_node.bl_idname)
        #     # Normal map
        #     if normal_node.bl_idname == "ShaderNodeNormal":
        #         normal_map_params = texture_or_value(parent, mat.inputs["Normal"], filepath, is_normal_map = True)
        #         if len(normal_map_params) != 0:
        #             # Normal map added if found
        #             parent.report({'INFO'}, "Add normal map!")
        #             mat_data["normal_map"] = normal_map_params
    
        foreach  texture_or_value(parent, mat.inputs["Weight"], filepath)
            print

        # Export as diffuse
        diffuse_mat = {
            "type" : "diffuse",
            "albedo" : texture_or_value(parent, mat.inputs["Base Color"], filepath),
            "roughness" : texture_or_value(parent, mat.inputs["Roughness"], filepath),
            "alpha":  texture_or_value(parent, mat.inputs["Alpha"], filepath),
        }
        if "Weight" in mat.inputs:
            diffuse_mat["translucency"] = texture_or_value(parent, mat.inputs["Weight"], filepath),
        # Export as blend
        local_material = {}
        if scene.improved_principled:
            local_material["type"] = "blend"
            local_material["alpha"] = texture_or_value(parent, mat.inputs["Metallic"], filepath)
            local_material["matA"] = {
                "type" : "metal",
                "ks" : texture_or_value(parent, mat.inputs["Base Color"], filepath),
                "roughness" : texture_or_value(parent, mat.inputs["Roughness"], filepath),
                "specular": texture_or_value(parent, mat.inputs["Specular"], filepath),
                "anisotropic": texture_or_value(parent, mat.inputs["Anisotropic"], filepath),
                "anisotropic_rotation": texture_or_value(parent, mat.inputs["Anisotropic Rotation"], filepath),
            }
            local_material["matB"] = diffuse_mat
            # if mat.inputs["Transmission"].default_value > 0.0:
            #     parent.error_or_warning = True
            #     parent.report({'WARNING'}, " Transmission not supported")
            
        else:
            local_material = diffuse_mat
            # local_material["type"] = "diffuse"
            # local_material["albedo"] = texture_or_value(parent, mat.inputs[0], filepath)
        # Add normal map
        if texture_might_exist(mat.inputs["Normal"]):
            normal_node = mat.inputs["Normal"].links[0].from_node
            parent.report({'INFO'}, "Normal type : " + normal_node.bl_idname)
            # Normal map
            if normal_node.bl_idname == "ShaderNodeNormal":
                normal_map_params = texture_or_value(parent, mat.inputs["Normal"], filepath, is_normal_map = True)
                if len(normal_map_params) != 0:
                    # Normal map added if found
                    parent.report({'INFO'}, "Add normal map!")
                    if scene.improved_principled:
                        local_material["matA"] = normal_map_params
                        local_material["matB"] = normal_map_params
                    else:
                        local_material["normal_map"] = normal_map_params
        # exported_normal = False
        # if scene.export_normal_map: 
        #     # TODO: Export other parameters
        #     if texture_might_exist(mat.inputs["Normal"]):
        #         normal_map_params = texture_or_value(parent, mat.inputs["Normal"], filepath, is_normal_map = True)
        #         if len(normal_map_params) != 0:
        #             # Normal map added if found
        #             mat_data["type"] = "normal_map"
        #             mat_data["normal_map"] = normal_map_params
        #             mat_data["material"] = local_material
        #             exported_normal = True
        #         else:
        #             # Ignore normal map
        #             parent.report({'WARNING'}, "Normal map ignored -- wrong node topology")
        # if not exported_normal:
        #     mat_data = local_material
        mat_data = local_material
            
    else:
        parent.report({'WARNING'}, f"Wrong material: {rootMaterial.name} | type: {mat.bl_idname}")
        parent.error_or_warning = True
        mat_data["type"] = "diffuse"

    # Give name 
    # parent.report({'INFO'}, "set name of mat a : " + mat.bl_idname)
    # parent.report({'INFO'}, "set name of mat b : " + rootMaterial.name)
    mat_data["name"] = rootMaterial.name
    if not ("type" in mat_data):
        parent.report({'WARNING'}, f"Wrong material: {rootMaterial.name} | type: {mat.bl_idname} | json: {mat_data}")
        parent.error_or_warning = True
        mat_data["type"] = "diffuse"
        mat_data["albedo"] = [0.8, 0.0, 0.8]
    return mat_data

def export_material(parent, scene, material, filepath):
    if material is None:
        parent.report({'WARNING'}, " no material on object")
        parent.error_or_warning = True
    mats = []
    parent.report({'INFO'}, f'Exporting material named: {material.name}')
    currentMaterial = None
    material.use_nodes = True
        
    if material and material.use_nodes: 
        for node in material.node_tree.nodes:
            if node.type == "OUTPUT_MATERIAL":
                for input in node.inputs:
                    for node_links in input.links:
                        currentMaterial =  node_links.from_node
                        mats += [export_material_node(parent, scene, currentMaterial, material, filepath)]
    return mats

def write_obj(file, mesh, indices, normals, i):
    # Pack U,V
    uvs = []
    for uv_layer in mesh.uv_layers:
        for tri in mesh.loop_triangles:
            if tri.material_index == i:
                for loop_index in tri.loops:
                    uvs.append((
                        uv_layer.data[loop_index].uv[0],
                        uv_layer.data[loop_index].uv[1]
                    ))

    # write obj
    out = open(file, 'w')

    # write vertices positions
    for id_vertex in indices:
        out.write('v {:.6f} {:.6f} {:.6f}\n'.format( 
            mesh.vertices[id_vertex].co.x, 
            mesh.vertices[id_vertex].co.y, 
            mesh.vertices[id_vertex].co.z)
        )
    
    for n in normals:
        out.write('vn {:.6f} {:.6f} {:.6f}\n'.format(n[0], n[1], n[2]))

    if len(uvs) != 0:
        for id in range(len(indices)):
            out.write('vt {:.6f} {:.6f}\n'.format(uvs[id][0],  uvs[id][1]))

    # write f: ver ind/ uv ind
    for i in range(0, len(indices), 3):
        if len(uvs) != 0:
            out.write(f'f {i+1}/{i+1}/{i+1} {i+2}/{i+2}/{i+2} {i+3}/{i+3}/{i+3}\n')
        else:
           out.write(f'f {i+1}//{i+1} {i+2}//{i+2} {i+3}//{i+3}\n')

def export_objects(parent, filepath, scene, frameNumber):
    materials = [
        {"type" : "diffuse", "name" : "DEFAULT", "albedo" : [0.8, 0.8, 0.8]}
    ]
    shapes = []
    
    # Compute the objects to export
    objects = []
    total = 0
    for object in scene.objects:
        if object.hide_render:
            parent.report({'INFO'}, f"Skipping hidden object: {object.name}")
            continue
        if object is not None and object.type == 'MESH':
            objects.append(object)
            total += 1
    
    # Get the window manager
    wm = bpy.context.window_manager
    wm.progress_begin(0, total)
        
    
    for (j, object) in enumerate(objects):
        # Export the object
        parent.report({'INFO'}, f"Exporting Object: {object.name}")
        wm.progress_update(j)
        
        # Apply modifiers
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.context.view_layer.update()
        object.data.update()
        dg = bpy.context.evaluated_depsgraph_get()
        eval_obj = object.evaluated_get(dg)
        mesh = eval_obj.to_mesh()
        if not mesh.loop_triangles and mesh.polygons:
            parent.report({'INFO'}, " loop triangles...")
            mesh.calc_loop_triangles()

        for i in range(max(len(object.material_slots), 1)):    
            # Export the material if needed
            if len(object.material_slots) != 0:
                material = object.material_slots[i].material
                if material.name not in exportedMaterials:
                    materials += export_material(parent, scene, material, filepath)
                    exportedMaterials.append(material.name)
                
        
            # Create ouput directory
            objFolderPath =  bpy.path.abspath(filepath + './meshes/' + frameNumber + '/')
            if not os.path.exists(objFolderPath):
                parent.report({"INFO"},f'Meshes directory did not exist, creating: {objFolderPath}')
                os.makedirs(objFolderPath)

            # Compute the path variables
            objName = object.name + f'_mat{i}.obj' 
            objName = objName.replace(":","_")
            objFilePath = objFolderPath + objName
            objFilePathRel = 'meshes/' + frameNumber + '/' + objName

            # Export obj manually
            indices = []
            normals = []
            if os.path.exists(objFilePath) and not scene.reexport_geometry:
                parent.report({'INFO'}, f"Skipping existing file: {objFilePath}")
            else:
                parent.report({'INFO'}, f"Exporting file: {objFilePath}")
                for loop_tri in mesh.loop_triangles:
                    polygon = mesh.polygons[loop_tri.polygon_index]
                    if polygon.material_index == i:
                        for loop_index in loop_tri.loops:
                            vertex_index = mesh.loops[loop_index].vertex_index
                            indices.append(vertex_index)
                            normals.append(mesh.loops[loop_index].normal)
                parent.report({'DEBUG'}, f"Exporting - Nb Tri: {len(indices) // 3}") 
                if(len(indices) == 0):
                    continue 
                write_obj(objFilePath, mesh, indices, normals, i)

            # Create entry
            # TODO: Manage participating media
            #exportObject_medium(scene_file, object.material_slots[0].material)
            shape_data = {}
            shape_data["type"] = "mesh"
            shape_data["filename"] = objFilePathRel
            if len(object.material_slots) != 0:
                shape_data["material"] = object.material_slots[i].material.name
            else:
                # Use the default material
                shape_data["material"] = "DEFAULT"
            
            matrix =  object.matrix_world # transposed()
            shape_data["transform"] = {
                "matrix" : [
                    matrix[0][0],matrix[0][1],matrix[0][2],matrix[0][3],
                    matrix[1][0],matrix[1][1],matrix[1][2],matrix[1][3],
                    matrix[2][0],matrix[2][1],matrix[2][2],matrix[2][3],
                    matrix[3][0],matrix[3][1],matrix[3][2],matrix[3][3]
                ]
            }                
            shapes += [shape_data]
    wm.progress_end()

    return (shapes, materials)
            

def export_integrator(parent, scene):
    int_data = {}
    if scene.integrators == 'path':
        int_data["type"] = "path"
        int_data["max_depth"] = scene.path_integrator_max_depth
    elif scene.integrators == 'normal':
        int_data["type"] = "normal"
    elif scene.integrators == "ao":
        int_data["type"] = "ao"
    else: 
        parent.report({'WARNING'}, " Wrong type of integrator")
        parent.error_or_warning = True
        int_data["type"] = "path" # Default
    return int_data

def export_background(parent, scene, filepath):
    # Fetch world
    outputNode = None
    for n in scene.world.node_tree.nodes:
        if n.type == 'OUTPUT_WORLD':
            outputNode = n
            break
    
    # No connection
    if len(outputNode.inputs[0].links) == 0:
        return 0.0
    
    # Get the node
    node = outputNode.inputs[0].links[0].from_node
    if node.bl_idname == "ShaderNodeBackground":
        if scene.envmap:
            return texture_or_value(parent, node.inputs[0], filepath)
        else: 
            return only_value(parent, node.inputs[0])
    else:
        parent.report({'WARNING'}, f"Unsupported background node: {node.bl_idname}")
        parent.error_or_warning = True
        return [0.0, 0.0, 0.0]

def export_renderer(parent, filepath, scene , frameNumber):
    if filepath == "":
        filepath = os.path.dirname(bpy.data.filepath)
        parent.report({'WARNING'}, f"No output directory, using default: {filepath}")
    else:
        parent.report({'INFO'}, f"Exporting to: {filepath}")

    # Create output directory
    out = os.path.join(filepath, "test" + frameNumber +".json")
    if not os.path.exists(filepath):
        parent.report({'INFO'}, f'Output directory did not exist, creating: {filepath}')
        os.makedirs(filepath)

    # Create texture directory
    if not os.path.exists(filepath + "/textures"):
        parent.report({'INFO'}, f'Texture directory did not exist, creating: {filepath + "/textures"}')
        os.makedirs(filepath + "/textures")
    
    # Unpack all the local ressources
    # bpy.ops.file.unpack_all(method='USE_LOCAL')

    # Clear lsit of cached texture and materials
    exportedMaterials.clear()

    with open(out, 'w') as scene_file:
        data_all ={}
        
        data_all["background"] = export_background(parent, scene, filepath)
        data_all["integrator"] = export_integrator(parent, scene)
        (camera, sampler) = export_camera(parent, scene)
        data_all["camera"] = camera
        data_all["sampler"] = sampler
        (shapes, materials) = export_objects(parent, filepath, scene, frameNumber)
        data_all["materials"] = materials
        data_all["shapes"] = shapes

        exported_json_string = json.dumps(data_all, indent=4)
        scene_file.write(exported_json_string)
        scene_file.close()
