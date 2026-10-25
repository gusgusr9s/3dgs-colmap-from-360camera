"""World transforms for attached collision boxes, matching Three Euler XYZ."""
import math


def rotation_matrix(rotation):
    x,y,z = rotation
    sx,cx,sy,cy,sz,cz = math.sin(x),math.cos(x),math.sin(y),math.cos(y),math.sin(z),math.cos(z)
    return [[cy*cz,-cy*sz,sy],[sx*sy*cz+cx*sz,-sx*sy*sz+cx*cz,-sx*cy],[-cx*sy*cz+sx*sz,cx*sy*sz+sx*cz,cx*cy]]


def transform_vector(matrix, vector):
    return [sum(row[i]*vector[i] for i in range(3)) for row in matrix]


def world_box(box, objects):
    rotation = rotation_matrix(box.get('rotation', [0,box.get('yaw',0),0]))
    position = box['position']; size = box['size']
    parent = next((item for item in objects if item['id'] == box.get('parent')),None)
    if parent:
        owner_rotation = rotation_matrix(parent['rotation'])
        position = [p+v*parent['scale'] for p,v in zip(parent['position'],transform_vector(owner_rotation,position))]
        rotation = [[sum(owner_rotation[r][k]*rotation[k][c] for k in range(3)) for c in range(3)] for r in range(3)]
        size = [value*parent['scale'] for value in size]
    return position,rotation,size
