from UR3e_rg2.BulletGym import *
from math import pi, asin
from time import sleep
from importlib.resources import files
from typing import Dict

class UR3eRG2():

    def __init__(self) -> None:
        urdf_path = str(files('UR3e_rg2').joinpath('robot_description_files/ur3e_rg2/ur3e_rg2.urdf'))
        self.bullet_gym = BulletGym(urdf_path,
                                    movable_joints_indexes= [1,2,3,4,5,6,9,10,11,12,13,14],
                                    eef_link_index=7,gui=True, debug=True)
        a = self.bullet_gym.get_current_state()
        
        pass


    def move_gripper(self, width: float) -> None:
        curr_state = self.bullet_gym.get_current_state()        
        joint_vals = 49*pi/180 - asin((7.72089 + width)/ 110.68359)
        curr_state[6] = joint_vals
        curr_state[7] = joint_vals
        curr_state[8] = -joint_vals
        curr_state[9] = -joint_vals
        curr_state[10] = joint_vals
        curr_state[11] = -joint_vals
        self.bullet_gym.move_joints(curr_state)

    def move_robot(self, joint_values: list) -> None:
        self.bullet_gym.move_joints(joint_values)

    def get_param_values(self) -> dict:
        return self.bullet_gym.get_debug_params()
    
    def set_param_values(self, param_values: Dict) -> None:
        """Definir los valores iniciales de los sliders para adquirir los parámetros.

        Args:
            param_values (Dict): Diccionario con el siguiente formato {<nombre_joint>:<valor_inicial>,<nombre_joint>:<valor_inicial>,...}

        Raises:
            Exception: _description_
        """
        if len(self.bullet_gym.get_debug_params()) != len(param_values):
            raise Exception("Param sizes don't match.")
        self.bullet_gym.set_user_debug_params([BulletParameter(" "+name,2*pi,-2*pi,value) for name, value in param_values.items()])
    
    def interactive_joint_movement(self) -> None:
        while True:
            params = self.bullet_gym.get_debug_params()
            joint_values = [value for param_name, value in params.items() if "j" in param_name]
            self.bullet_gym.move_joints(joint_values)
            sleep(0.01)

if __name__=='__main__':
    robot_gym = UR3eRG2()
    # Test gripper
    for i in range(101):
        robot_gym.move_gripper(i)
        sleep(0.1)
    for i in range(101,0,-1):
        robot_gym.move_gripper(i)
        sleep(0.1)
    robot_gym.set_param_values({"j0":1,"j1":2,"j2":3,"j3":4,"j4":5,"j5":6,"j6":7,"j7":8,"j8":9,"j9":10,"j10":11,"j11":12})
    robot_gym.interactive_joint_movement()