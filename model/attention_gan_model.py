import torch
import itertools
from util.image_pool import ImagePool
from .base_model import BaseModel
from . import networks
####### Self-Supervised Task #######
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
####### Self-Supervised Task #######

class AttentionGANModel(BaseModel):
    @staticmethod
    def modify_commandline_options(parser, is_train=True):
        parser.set_defaults(no_dropout=True)  # default CycleGAN did not use dropout
        if is_train:
            parser.add_argument('--lambda_A', type=float, default=10.0, help='weight for cycle loss (A -> B -> A)')
            parser.add_argument('--lambda_B', type=float, default=10.0, help='weight for cycle loss (B -> A -> B)')
            parser.add_argument('--lambda_identity', type=float, default=0.5, help='use identity mapping. Setting lambda_identity other than 0 has an effect of scaling the weight of the identity mapping loss. For example, if the weight of the identity loss should be 10 times smaller than the weight of the reconstruction loss, please set lambda_identity = 0.1')

        return parser

    def __init__(self, opt):
        BaseModel.__init__(self, opt)
        # specify the training losses you want to print out. The training/test scripts will call <BaseModel.get_current_losses>
        self.loss_names = ['D_A', 'G_A', 'cycle_A', 'idt_A', 'D_B', 'G_B', 'cycle_B', 'idt_B']
        # specify the images you want to save/display. The training/test scripts will call <BaseModel.get_current_visuals>
        visual_names_A = ['real_A', 'fake_B', 'rec_A', 'o1_b', 'o2_b', 'o3_b', 'o4_b', 'o5_b', 'o6_b', 'o7_b', 'o8_b', 'o9_b', 'o10_b',
        'a1_b', 'a2_b', 'a3_b', 'a4_b', 'a5_b', 'a6_b', 'a7_b', 'a8_b', 'a9_b', 'a10_b', 'i1_b', 'i2_b', 'i3_b', 'i4_b', 'i5_b', 
        'i6_b', 'i7_b', 'i8_b', 'i9_b']
        visual_names_B = ['real_B', 'fake_A', 'rec_B', 'o1_a', 'o2_a', 'o3_a', 'o4_a', 'o5_a', 'o6_a', 'o7_a', 'o8_a', 'o9_a', 'o10_a', 
        'a1_a', 'a2_a', 'a3_a', 'a4_a', 'a5_a', 'a6_a', 'a7_a', 'a8_a', 'a9_a', 'a10_a', 'i1_a', 'i2_a', 'i3_a', 'i4_a', 'i5_a', 
        'i6_a', 'i7_a', 'i8_a', 'i9_a']
        if self.isTrain and self.opt.lambda_identity > 0.0:  # if identity loss is used, we also visualize idt_B=G_A(B) ad idt_A=G_A(B)
            visual_names_A.append('idt_B')
            visual_names_B.append('idt_A')

        if self.opt.saveDisk:
            self.visual_names = ['real_A', 'fake_B', 'a10_b', 'real_B','fake_A', 'a10_a']
        else:
            self.visual_names = visual_names_A + visual_names_B  # combine visualizations for A and B
        # specify the models you want to save to the disk. The training/test scripts will call <BaseModel.save_networks> and <BaseModel.load_networks>.
        if self.isTrain:
            self.model_names = ['G_A', 'G_B', 'D_A', 'D_B']
        else:  # during test time, only load Gs
            self.model_names = ['G_A', 'G_B']

        # define networks (both Generators and discriminators)
        # The naming is different from those used in the paper.
        # Code (vs. paper): G_A (G), G_B (F), D_A (D_Y), D_B (D_X)
        self.netG_A = networks.define_G(opt.input_nc, opt.output_nc, opt.ngf, 'our', opt.norm,
                                        not opt.no_dropout, opt.init_type, opt.init_gain, self.gpu_ids)
        self.netG_B = networks.define_G(opt.output_nc, opt.input_nc, opt.ngf, 'our', opt.norm,
                                        not opt.no_dropout, opt.init_type, opt.init_gain, self.gpu_ids)

        if self.isTrain:  # define discriminators
            self.netD_A = networks.define_D(opt.output_nc, opt.ndf, opt.netD,
                                            opt.n_layers_D, opt.norm, opt.init_type, opt.init_gain, self.gpu_ids)
            self.netD_B = networks.define_D(opt.input_nc, opt.ndf, opt.netD,
                                            opt.n_layers_D, opt.norm, opt.init_type, opt.init_gain, self.gpu_ids)

        if self.isTrain:
            if opt.lambda_identity > 0.0:  # only works when input and output images have the same number of channels
                assert(opt.input_nc == opt.output_nc)
            self.fake_A_pool = ImagePool(opt.pool_size)  # create image buffer to store previously generated images
            self.fake_B_pool = ImagePool(opt.pool_size)  # create image buffer to store previously generated images
            # define loss functions
            self.criterionGAN = networks.GANLoss(opt.gan_mode).to(self.device)  # define GAN loss.
            self.criterionCycle = torch.nn.L1Loss()
            self.criterionIdt = torch.nn.L1Loss()
            # initialize optimizers; schedulers will be automatically created by function <BaseModel.setup>.
            self.optimizer_G = torch.optim.Adam(itertools.chain(self.netG_A.parameters(), self.netG_B.parameters()), lr=opt.lr, betas=(opt.beta1, 0.999))
            self.optimizer_D = torch.optim.Adam(itertools.chain(self.netD_A.parameters(), self.netD_B.parameters()), lr=opt.lr, betas=(opt.beta1, 0.999))
            self.optimizers.append(self.optimizer_G)
            self.optimizers.append(self.optimizer_D)

    def set_input(self, input):
        """Unpack input data from the dataloader and perform necessary pre-processing steps.
        Parameters:
            input (dict): include the data itself and its metadata information.
        The option 'direction' can be used to swap domain A and domain B.
        """
        AtoB = self.opt.direction == 'AtoB'
        self.real_A = input['A' if AtoB else 'B'].to(self.device)
        self.real_B = input['B' if AtoB else 'A'].to(self.device)
        self.image_paths = input['A_paths' if AtoB else 'B_paths']

    def forward(self):
        """Run forward pass; called by both functions <optimize_parameters> and <test>."""
        self.fake_B, self.o1_b, self.o2_b, self.o3_b, self.o4_b, self.o5_b, self.o6_b, self.o7_b, self.o8_b, self.o9_b, self.o10_b, \
        self.a1_b, self.a2_b, self.a3_b, self.a4_b, self.a5_b, self.a6_b, self.a7_b, self.a8_b, self.a9_b, self.a10_b, \
        self.i1_b, self.i2_b, self.i3_b, self.i4_b, self.i5_b, self.i6_b, self.i7_b, self.i8_b, self.i9_b = self.netG_A(self.real_A)  # G_A(A)
        self.rec_A, _, _, _, _, _, _, _, _, _, _, \
        _, _, _, _, _, _, _, _, _, _, \
        _, _, _, _, _, _, _, _, _ = self.netG_B(self.fake_B)   # G_B(G_A(A))
        ####### Self-Supervised Task #######
        self.rotate_real_A = self.rotate_image(self.real_A)
        self.rotate_fake_B = self.rotate_image(self.fake_B)
        # self.rotate_rec_A = self.rotate_image(self.rec_A)
        ####### Self-Supervised Task #######
        self.fake_A, self.o1_a, self.o2_a, self.o3_a, self.o4_a, self.o5_a, self.o6_a, self.o7_a, self.o8_a, self.o9_a, self.o10_a, \
        self.a1_a, self.a2_a, self.a3_a, self.a4_a, self.a5_a, self.a6_a, self.a7_a, self.a8_a, self.a9_a, self.a10_a, \
        self.i1_a, self.i2_a, self.i3_a, self.i4_a, self.i5_a, self.i6_a, self.i7_a, self.i8_a, self.i9_a = self.netG_B(self.real_B)  # G_B(B)
        self.rec_B, _, _, _, _, _, _, _, _, _, _, \
        _, _, _, _, _, _, _, _, _, _, \
        _, _, _, _, _, _, _, _, _ = self.netG_A(self.fake_A)   # G_A(G_B(B))
        ####### Self-Supervised Task #######
        self.rotate_real_B = self.rotate_image(self.real_B)
        self.rotate_fake_A = self.rotate_image(self.fake_A)
        # self.rotate_rec_B = self.rotate_image(self.rec_B)
        ####### Self-Supervised Task #######

    def backward_D_basic(self, netD, real, fake, rotate_real, rotate_fake, weight_rotation_loss_d):
        """Calculate GAN loss for the discriminator
        Parameters:
            netD (network)      -- the discriminator D
            real (tensor array) -- real images
            fake (tensor array) -- images generated by a generator
        Return the discriminator loss.
        We also call loss_D.backward() to calculate the gradients.
        """
        # Real
        ####### Self-Supervised Task #######   
        _, pred_real, pred_rotate_real, _ = netD(rotate_real)
        #_, pred_real, pred_rotate_real, _ = netD(real, rotate_real)
        ####### Self-Supervised Task ####### 
        loss_D_real = self.criterionGAN(pred_real, True)
        # Fake
        ####### Self-Supervised Task #######   
        _, pred_fake, _, _ = netD(rotate_fake.detach())
        #_, pred_fake, rotation_fake, _ = netD(fake.detach(), rotate_fake)
        ####### Self-Supervised Task #######   
        loss_D_fake = self.criterionGAN(pred_fake, False)
        # Combined loss and calculate gradients
        loss_D = (loss_D_real + loss_D_fake) * 0.5

        ####### Self-Supervised Task #######
        self.d_class_loss = self.auxiliary_ls_rotation_loss(real.size(0), pred_rotate_real)
        loss_D, rotation_loss = self.add_rotation_loss_d(self.d_class_loss, loss_D, weight_rotation_loss_d)
        ####### Self-Supervised Task #######
        loss_D.backward()
        return loss_D, rotation_loss

    def backward_D_A(self):
        """Calculate GAN loss for discriminator D_A"""
        fake_B = self.fake_B_pool.query(self.fake_B)
        self.loss_D_A, self.rotation_loss_D_A = self.backward_D_basic(self.netD_A, self.real_B, fake_B, self.rotate_real_B, self.rotate_fake_B, self.opt.weight_rotation_loss_g * 5)

    def backward_D_B(self):
        """Calculate GAN loss for discriminator D_B"""
        fake_A = self.fake_A_pool.query(self.fake_A)
        self.loss_D_B, self.rotation_loss_D_B = self.backward_D_basic(self.netD_B, self.real_A, fake_A, self.rotate_real_A, self.rotate_fake_A, self.opt.weight_rotation_loss_g * 5)

    def backward_G(self):
        """Calculate the loss for generators G_A and G_B"""
        lambda_idt = self.opt.lambda_identity
        lambda_A = self.opt.lambda_A
        lambda_B = self.opt.lambda_B
        # Identity loss
        if lambda_idt > 0:
            # G_A should be identity if real_B is fed: ||G_A(B) - B||
            self.idt_A, _, _, _, _, _, _, _, _, _, _, \
            _, _, _, _, _, _, _, _, _, _, \
            _, _, _, _, _, _, _, _, _  = self.netG_A(self.real_B)
            self.loss_idt_A = self.criterionIdt(self.idt_A, self.real_B) * lambda_B * lambda_idt
            # G_B should be identity if real_A is fed: ||G_B(A) - A||
            self.idt_B, _, _, _, _, _, _, _, _, _, _, \
            _, _, _, _, _, _, _, _, _, _, \
            _, _, _, _, _, _, _, _, _  = self.netG_B(self.real_A)
            self.loss_idt_B = self.criterionIdt(self.idt_B, self.real_A) * lambda_A * lambda_idt
        else:
            self.loss_idt_A = 0
            self.loss_idt_B = 0

        ####### Self-Supervised Task #######
        # GAN loss D_A(G_A(A))
        _, pred_fake_A, rotation_fake_A, _ = self.netD_A(self.rotate_fake_B)
        #_, pred_fake_A, rotation_fake_A, _ = self.netD_A(self.fake_B, self.rotate_fake_B)
        self.loss_G_A = self.criterionGAN(pred_fake_A, True)
        # GAN loss D_B(G_B(B))
        _, pred_fake_B, rotation_fake_B, _ = self.netD_B(self.rotate_fake_A)
        #_, pred_fake_B, rotation_fake_B, _ = self.netD_B(self.fake_A, self.rotate_fake_A)
        self.loss_G_B = self.criterionGAN(pred_fake_B, True)
        # Forward cycle loss || G_B(G_A(A)) - A||
        self.loss_cycle_A = self.criterionCycle(self.rec_A, self.real_A) * lambda_A
        # Backward cycle loss || G_A(G_B(B)) - B||
        self.loss_cycle_B = self.criterionCycle(self.rec_B, self.real_B) * lambda_B
        # combined loss and calculate gradients
        self.loss_G = self.loss_G_A + self.loss_G_B + self.loss_cycle_A + self.loss_cycle_B + self.loss_idt_A + self.loss_idt_B

        self.g_class_loss_A = self.auxiliary_ls_rotation_loss(self.fake_B.size(0), rotation_fake_A)
        self.loss_G = self.add_rotation_loss_g(self.g_class_loss_A, self.loss_G, self.opt.weight_rotation_loss_g)
        self.g_class_loss_B = self.auxiliary_ls_rotation_loss(self.fake_A.size(0), rotation_fake_B)
        self.loss_G = self.add_rotation_loss_g(self.g_class_loss_B, self.loss_G, self.opt.weight_rotation_loss_g)
        ####### Self-Supervised Task #######
        
        self.loss_G.backward()
    
    ####### Self-Supervised Task #######   
    def rotate_image(self, input):
        rotation_90 = input.transpose(2,3)
        rotation_180 = input.flip(2,3)
        rotation_270 = input.transpose(2,3).flip(2,3)
        return torch.cat((input, rotation_90, rotation_180, rotation_270), 0)

    def auxiliary_ls_rotation_loss(self, batch_len, input):
        rotation_labels = torch.zeros(batch_len * 4, dtype=torch.int64).to(self.device)
        for i in range(batch_len * 4):
            if i < batch_len:
                rotation_labels[i] = 0
            elif i < batch_len * 2:
                rotation_labels[i] = 1
            elif i < batch_len * 3:
                rotation_labels[i] = 2
            else:
                rotation_labels[i] = 3

        rotation_labels_one_hot = F.one_hot(rotation_labels.to(torch.int64), 4).float()
        class_loss = torch.sum(F.mse_loss(input, rotation_labels_one_hot.unsqueeze(2).unsqueeze(3).expand_as(input)))

        return class_loss

    def add_rotation_loss_d(self, class_loss, loss_D, weight_rotation_loss_d):
        #weight_rotation_loss_d = self.opt.weight_rotation_loss_d
        rotation_loss = weight_rotation_loss_d * class_loss
        loss_D += weight_rotation_loss_d * class_loss
        return loss_D, rotation_loss

    def add_rotation_loss_g(self, class_loss, loss_G, weight_rotation_loss_g):
        #weight_rotation_loss_g = self.opt.weight_rotation_loss_g
        loss_G += weight_rotation_loss_g * class_loss
        return loss_G

    def transfer_batch_size(self, batch_len):
        self.batch_len = batch_len
    
    def transfer_rotation_loss(self):
        d_class_losses_A = self.rotation_loss_D_A
        d_class_losses_B = self.rotation_loss_D_B
        g_class_loss_A = self.g_class_loss_A * self.opt.weight_rotation_loss_g
        g_class_loss_B = self.g_class_loss_B * self.opt.weight_rotation_loss_g
        return d_class_losses_A, d_class_losses_B, g_class_loss_A, g_class_loss_B
    ####### Self-Supervised Task #######   

    def optimize_parameters(self):
        """Calculate losses, gradients, and update network weights; called in every training iteration"""
        # forward
        self.forward()      # compute fake images and reconstruction images.
        # G_A and G_B
        self.set_requires_grad([self.netD_A, self.netD_B], False)  # Ds require no gradients when optimizing Gs
        self.optimizer_G.zero_grad()  # set G_A and G_B's gradients to zero
        self.backward_G()             # calculate gradients for G_A and G_B
        self.optimizer_G.step()       # update G_A and G_B's weights
        # D_A and D_B
        self.set_requires_grad([self.netD_A, self.netD_B], True)
        self.optimizer_D.zero_grad()   # set D_A and D_B's gradients to zero
        self.backward_D_A()      # calculate gradients for D_A
        self.backward_D_B()      # calculate graidents for D_B
        self.optimizer_D.step()  # update D_A and D_B's weights