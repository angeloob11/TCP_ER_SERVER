import pybullet as p
from typing import List, TypedDict, Any, Literal
from dataclasses import dataclass
from pybullet_planning import (
    get_collision_fn, get_extend_fn, get_sample_fn,
    get_difference_fn, rrt_connect
)
from time import sleep
from math import pi
import numpy as np
from random import randint

@dataclass
class BulletParameter():
    name: str
    max_val: float
    min_val: float
    start_val: float

class BulletGym():
    def __init__(self, urdf_path: str, movable_joints_indexes: list, eef_link_index: int, gui: bool=False, debug: bool=False) -> None:
        self.gui = gui
        self.urdf_path = urdf_path
        if gui:
            self.client = p.connect(p.GUI)            
        else:
            self.client = p.connect(p.DIRECT)
        
        self.robot_id = p.loadURDF(urdf_path, [0, 0, 0], useFixedBase=True,
                                   flags=p.URDF_USE_SELF_COLLISION)

        floor_shape = p.createCollisionShape(p.GEOM_PLANE)

        # Add floor in bullet
        floor_visual_shape = p.createVisualShape(p.GEOM_PLANE, rgbaColor=[0.5, 0.5, 0.5, 1])
        self.floor_id = p.createMultiBody(baseCollisionShapeIndex=floor_shape, baseVisualShapeIndex=floor_visual_shape)
        
        self.link_num = 11

        self.added_object_ids = []
        
        self.movable_joints = movable_joints_indexes

        self.eef_link_index = eef_link_index

        self.debug_index = []
        
        self.first_debug_render = False
        self.ordered_link_names = self.get_links()

        if gui and debug:
            debug_parameters = []

            for index, joint in enumerate(self.movable_joints):
                debug_parameters.append(BulletParameter(f" j{index}", 2*pi, -2*pi, 0))

            self.set_user_debug_params(debug_parameters)
          
        # Planning data 
        self.collision_fn = get_collision_fn(self.robot_id, self.movable_joints, obstacles=[self.floor_id])
        self.extend_fn = get_extend_fn(self.robot_id, self.movable_joints)
        self.sample_fn = get_sample_fn(self.robot_id, self.movable_joints)
        self.difference_fn = get_difference_fn(self.robot_id, self.movable_joints)

    
    def get_debug_params(self) -> dict:
        if not self.gui:
            return {}
        
        ret_dict = {}
        for name, id in self.debug_parameters.items():
            ret_dict.update({name:p.readUserDebugParameter(id)})
        
        return ret_dict
    
    def spawn_robot(self, pose: list) -> int:
        robot_id = p.loadURDF(self.urdf_path, [0, 0, 0], useFixedBase=True,
                                   flags=p.URDF_USE_SELF_COLLISION)

        orientation = p.getQuaternionFromEuler(pose[3:])  # New desired orientation (can be the same as before or different)
        p.resetBasePositionAndOrientation(robot_id, pose[:3], orientation)

        return robot_id

    def set_user_debug_params(self, params: List[BulletParameter]) -> None:
        p.removeAllUserParameters()
        self.debug_parameters = {}
        for parameter in params:
            self.debug_parameters.update({parameter.name:p.addUserDebugParameter(parameter.name, parameter.min_val, parameter.max_val, parameter.start_val)})

    def get_links(self) -> dict:
        _link_name_to_index = {p.getBodyInfo(self.robot_id)[0].decode('UTF-8'):-1,}
        
        for _id in range(p.getNumJoints(self.robot_id)):
            _name = p.getJointInfo(self.robot_id, _id)[12].decode('UTF-8')
            _link_name_to_index[_name] = _id
        
        return _link_name_to_index

    def get_joint_info(self) -> list:
        ret_list = []
        for i in range(p.getNumJoints(self.robot_id)):
            ret_list.append(p.getJointInfo(self.robot_id, i))

        return ret_list
    
    def get_current_state(self) -> list:
        return [x[0] for x in p.getJointStates(self.robot_id, self.movable_joints)]

    def move_joints(self, joint_values: list, robot_id: int, show_trail: bool = False) -> None:
        len_joint_val = len(joint_values)
        len_movable_joints = len(self.movable_joints)
        if len_joint_val != len_movable_joints:
            joint_values += self.get_current_state()[len_joint_val:]
        for i, joint_index in enumerate(self.movable_joints):
            p.resetJointState(robot_id, joint_index, targetValue=joint_values[i])
        if show_trail:              
            eef_position = self.forward_kin()[0][:3]  
            self.create_sphere(0.01,eef_position)

    def move_one_joint(self, joint_id: int, rads: float) -> None:
        p.resetJointState(self.robot_id, joint_id, targetValue=rads)

    def forward_kin(self, link_indexes: list = []) -> list:        
        if not link_indexes:
            link_indexes = [self.eef_link_index]
        ret_list = []
        link_states = p.getLinkStates(self.robot_id, link_indexes)
        for link_state in link_states:
            ret_list.append(list(link_state[0]+(link_state[1][-1],)+link_state[1][:-1]))
        
        return ret_list

    def plan_traj_rrt_connect(self, q0: list, q1: list, attemps: int=10, best: bool = False) -> list:
        paths = []
        for i in range(attemps):
            path = rrt_connect(q0, q1, distance_fn=self.difference_fn, sample_fn=self.sample_fn, 
                        extend_fn=self.extend_fn, collision_fn=self.collision_fn, max_iterations=10000,
                        max_time=2)
            if path and not best:
                return path
            elif path is not None:
                paths.append(path)
            elif path is None:
                np.random.seed(randint(0,4294967296))
        path_lens = [len(x) for x in paths]

        return paths[path_lens.index(min(path_lens))] if paths else paths

    def execute_path(self, path: list, time_between_steps: float = 0.01, show_trail: bool = False) -> None:
        if show_trail:
            self.remove_objects()
        for q in path:
            self.move_joints(q)
            if show_trail:              
                eef_position = self.forward_kin()[0][:3]  
                self.create_sphere(0.01,eef_position)
            sleep(time_between_steps)
