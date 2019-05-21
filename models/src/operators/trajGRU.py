import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from .cnn2D import CNN2D_cell, DeCNN2D_cell

class flow_warp(nn.Module):
    '''
    Arguments:
        The subcnn model and the M warp function 
    '''
    def __init__(self, channel_input, channel_output, link_size, batch_norm=False, device=None, value_dtype=None):
        super().__init__()
        self.device = device
        self.value_dtype = value_dtype
        self.channel_input = channel_input
        self.channel_output = channel_output
        self.link_size = link_size
        # 2 cnn layers
        displacement_layers = []
        displacement_layers.append(nn.Conv2d(channel_input+channel_output, 32, 5, 1, 2))
        displacement_layers.append(nn.LeakyReLU(negative_slope=0.2))
        displacement_layers.append(nn.Conv2d(32, link_size*2, 5, 1, 2))
        # displacement_layers.append(nn.LeakyReLU(negative_slope=0.2))

        # initialize the weightings in each layers.
        # nn.init.orthogonal_(displacement_layers[0].weight)

        # nn.init.kaiming_normal_(displacement_layers[0].weight, a=0.2, mode='fan_in', nonlinearity='leaky_relu')
        # nn.init.kaiming_normal_(displacement_layers[2].weight, a=0, mode='fan_in', nonlinearity='leaky_relu')
        nn.init.zeros_(displacement_layers[0].weight)
        nn.init.zeros_(displacement_layers[2].weight)
        nn.init.zeros_(displacement_layers[0].bias)
        nn.init.zeros_(displacement_layers[2].bias)
        self.displacement_layers = nn.Sequential(*displacement_layers)

    def grid_sample(self, x, flow):
        '''
        Function for sampling pixels based on given grid data.
        '''
        input_ = x
        b, _, h, w = input_.shape

        u, v = flow[:,0:self.link_size,:,:], flow[:,self.link_size:,:,:]
        y_, x_ = torch.meshgrid(torch.arange(h), torch.arange(w))
        y_, x_ = y_.expand(b,h,w).to(self.device, dtype=self.value_dtype), x_.expand(b,h,w).to(self.device, dtype=self.value_dtype)

        samples = []
        for i in range(self.link_size):
            new_x = (x_*2/w)-1 + u[:,i,:,:]
            new_y = (y_*2/h)-1 + v[:,i,:,:]
            grids = torch.stack([new_x, new_y], dim=3)
            samples.append(F.grid_sample(input_, grids))
        return torch.cat(samples, dim=1)

    def forward(self, x=None, prev_state=None):
        # get batch and spatial sizes
        # print('Prev:', prev_state.shape)
        input_ = x
        if input_ is None:
            stacked_inputs = prev_state
        else:
            stacked_inputs = torch.cat([input_, prev_state], dim=1)

        output = self.displacement_layers(stacked_inputs)
        output = self.grid_sample(x=prev_state, flow=output)

        return output

class TrajGRUcell(nn.Module):
    """
    Arguments: 
        This class is to generate a convolutional Traj_GRU cell.
    """
    def __init__(self, channel_input, channel_output, link_size, kernel=1, stride=1, padding=1, batch_norm=False, device=None, value_dtype=None):
        super().__init__()
        self.device = device
        self.value_dtype = value_dtype
        self.channel_input = channel_input
        self.channel_output = channel_output
        self.link_size = link_size

        self.reset_gate = CNN2D_cell(channel_input, channel_output, kernel, stride, padding, batch_norm)
        self.update_gate = CNN2D_cell(channel_input, channel_output, kernel, stride, padding, batch_norm)
        self.out_gate = CNN2D_cell(channel_input, channel_output, kernel, stride, padding, batch_norm, negative_slope=0.2)

        self.flow_warp = flow_warp(channel_input, channel_output, link_size, batch_norm, device, value_dtype)

        self.reset_gate_warp  = CNN2D_cell(channel_output*link_size, channel_output, 1, 1, 0, batch_norm)
        self.update_gate_warp  = CNN2D_cell(channel_output*link_size, channel_output, 1, 1, 0, batch_norm)
        self.out_gate_warp = CNN2D_cell(channel_output*link_size, channel_output, 1, 1, 0, batch_norm, negative_slope=0.2)


    def forward(self, x, prev_state=None):
        input_ = x

        # get batch and spatial sizes
        batch_size = input_.data.shape[0]
        H, W = input_.data.shape[2:]
        
        # generate empty prev_state, if None is provided
        if prev_state is None:
            state_size = (batch_size, self.channel_output, H, W)
            prev_state = torch.zeros(state_size).to(device=self.device, dtype=self.value_dtype)
        
        M = self.flow_warp(x=input_, prev_state=prev_state)
        
        # data size is [batch, channel, height, width]
        reset = torch.sigmoid(self.reset_gate(input_)+self.reset_gate_warp(M))
        update = torch.sigmoid(self.update_gate(input_)+self.update_gate_warp(M))
        out_inputs = F.leaky_relu(self.out_gate(input_)+reset*self.out_gate_warp(M), negative_slope=0.2)
        new_state = out_inputs*(1-update) + update*prev_state

        return new_state

class DeTrajGRUcell(nn.Module):
    """
    Arguments: 
        This class is to generate a deconvolutional Traj_GRU cell.
    """
    def __init__(self, channel_input, channel_output, link_size, kernel=1, stride=1, padding=1, batch_norm=False, device=None, value_dtype=None):
        super().__init__()
        self.device = device
        self.value_dtype = value_dtype
        self.channel_input = channel_input
        self.channel_output = channel_output
        self.link_size = link_size

        if channel_input != 0:
            self.reset_gate = DeCNN2D_cell(channel_input, channel_output, kernel, stride, padding, batch_norm)
            self.update_gate = DeCNN2D_cell(channel_input, channel_output, kernel, stride, padding, batch_norm)
            self.out_gate = DeCNN2D_cell(channel_input, channel_output, kernel, stride, padding, batch_norm, negative_slope=0.2)

        self.flow_warp = flow_warp(channel_input, channel_output, link_size, batch_norm, device, value_dtype)
        
        self.reset_gate_warp  = DeCNN2D_cell(channel_output*link_size, channel_output, 1, 1, 0, batch_norm, zeros_weight=True)
        self.update_gate_warp  = DeCNN2D_cell(channel_output*link_size, channel_output, 1, 1, 0, batch_norm, zeros_weight=True)
        self.out_gate_warp = DeCNN2D_cell(channel_output*link_size, channel_output, 1, 1, 0, batch_norm, zeros_weight=True)

    def forward(self, x=None, prev_state=None):
        input_ = x
        # get batch and spatial sizes
        if prev_state is not None:
            batch_size = prev_state.data.shape[0]
            H, W = prev_state.data.shape[2:]
        else:
            # generate empty prev_state, if None is provided
            state_size = (batch_size, self.channel_output, H, W)
            prev_state = torch.zeros(state_size).to(device=self.device, dtype=self.value_dtype)

        M = self.flow_warp(x=input_, prev_state=prev_state)
        
        # breakpoint()
        if self.channel_input == 0:
            reset = torch.sigmoid(self.reset_gate_warp(M))
            update = torch.sigmoid(self.update_gate_warp(M))
            out_inputs = F.leaky_relu(self.out_gate_warp(M)*reset, negative_slope=0.2)
            # out_inputs = torch.tanh(self.out_gate_warp(M)*reset)
        else:
            reset = torch.sigmoid(self.reset_gate(input_)+self.reset_gate_warp(M))
            update = torch.sigmoid(self.update_gate(input_)+self.update_gate_warp(M))
            out_inputs = F.leaky_relu(self.out_gate(input_)+reset*self.out_gate_warp(M), negative_slope=0.2)
            # out_inputs = torch.tanh(self.out_gate(input_)+reset*self.out_gate_warp(M))
        new_state = out_inputs*(1-update) + update*prev_state
        
        return new_state

class Encoder(nn.Module):
    def __init__(self, channel_input, channel_downsample, channel_gru, downsample_k, downsample_s, downsample_p,
                 gru_link_size, gru_k, gru_s, gru_p, n_cells, batch_norm=False, device=None, value_dtype=None):
        '''
        Argumensts:
        Generates a multi-layer convolutional GRU, which is called encoder.
        Preserves spatial dimensions across cells, only altering depth.
        ----------
        [Parameters]
        ----------
        channel_input: (integer.) channel size of input tensors.
        channel_downsample: (integer or list.) channel size of downsample layers.
        channel_gru: (integer or list.) channel size of gru layers.
        gru_link_size: (integer or list.) link size of subcnn layers in gru layers.
        downsample_k: (integer or list.) kernel size of downsample layers.
        downsample_s: (integer or list.) stride size of downsample layers.
        downsample_p: (integer or list.) padding size of downsample layers.
        gru_k: (integer or list.) kernel size of gru layers.
        gru_s: (integer or list.) stride size of gru layers.
        gru_p: (integer or list.) padding size of gru layers.
        n_cells: (integer.) number of chained "TRAJGRU".
        '''
        super().__init__()
        self.device = device
        self.value_dtype = value_dtype
        self.channel_input = channel_input

        ## set self variables  ##
        # channel size
        if type(channel_downsample) != list:
            channel_downsample = [channel_downsample]*n_cells
        assert len(channel_downsample) == n_cells, '"channel_downsample" must have the same length as n_cells'

        if type(channel_gru) != list:
            channel_gru = [channel_gru]*n_cells
        assert len(channel_gru) == n_cells, '"channel_gru" must have the same length as n_cells'

        if type(gru_link_size) != list:
            gru_link_size = [gru_link_size]*n_cells
        assert len(gru_link_size) == n_cells, '"gru_link_size" must have the same length as n_cells'

        # kernel size
        if type(downsample_k) != list:
            downsample_k = [downsample_k]*n_cells
        assert len(downsample_k) == n_cells, '"downsample_k" must have the same length as n_cells'
        # stride size
        if type(downsample_s) != list:
            downsample_s = [downsample_s]*n_cells
        assert len(downsample_s) == n_cells, '"downsample_s" must have the same length as n_cells'
        # padding size
        if type(downsample_p) != list:
            downsample_p = [downsample_p]*n_cells
        assert len(downsample_p) == n_cells, '"downsample_p" must have the same length as n_cells'

        if type(gru_k) != list:
            gru_k = [gru_k]*n_cells
        assert len(gru_k) == n_cells, '"gru_k" must have the same length as n_cells'

        if type(gru_s) != list:
            gru_s = [gru_s]*n_cells
        assert len(gru_s) == n_cells, '"gru_s" must have the same length as n_cells'

        if type(gru_p) != list:
            gru_p = [gru_p]*n_cells
        assert len(gru_p) == n_cells, '"gru_p" must have the same length as n_cells'

        self.n_cells = n_cells

        ## set encoder
        cells = []
        for i in range(n_cells):
            ## Downsample cell
            if i == 0:
                cell = CNN2D_cell(channel_input=channel_input, channel_output=channel_downsample[i], kernel=downsample_k[i], 
                                stride=downsample_s[i], padding=downsample_p[i], batch_norm=batch_norm)
            else:
                cell = CNN2D_cell(channel_input=channel_gru[i-1], channel_output=channel_downsample[i], kernel=downsample_k[i], 
                                stride=downsample_s[i], padding=downsample_p[i], batch_norm=batch_norm)

            name = 'Downsample_' + str(i).zfill(2)
            setattr(self, name, cell)
            cells.append(getattr(self, name))
            
            ## gru cell
            cell = TrajGRUcell(channel_input=channel_downsample[i], channel_output=channel_gru[i], link_size=gru_link_size[i],
                               kernel=gru_k[i], stride=gru_s[i], padding=gru_p[i], device=device, value_dtype=value_dtype)
            name = 'TrajGRUcell_' + str(i).zfill(2)
            setattr(self, name, cell)
            cells.append(getattr(self, name))

        self.cells = cells
    
    def forward(self, x=None, hidden=None):
        if hidden is None:
            hidden = [None]*self.n_cells

        input_ = x
        upd_hidden = []

        for i in range(self.n_cells):
            ## Convolution cell
            cell = self.cells[2*i]
            input_ = cell(input_)
            ## GRU cell
            cell = self.cells[2*i+1]
            cell_hidden = hidden[i]
            # TrajGRUcell(x=None, prev_state=None)
            upd_cell_hidden = cell(x=input_, prev_state=cell_hidden)
            upd_hidden.append(upd_cell_hidden)
            # Pass input_ to the next
            input_ = upd_cell_hidden
        # return new hidden state
        return upd_hidden

class Forecaster(nn.Module):
    def __init__(self, channel_input, channel_upsample, channel_gru, upsample_k, upsample_p, upsample_s,
                 gru_link_size, gru_k, gru_s, gru_p, n_cells, channel_output=1, output_k=1, output_s = 1, 
                 output_p=0, n_output_layers=1, batch_norm=False, device=None, value_dtype=None):
        '''
        Argumensts:
        Generates a multi-layer deconvolutional GRU, which is called forecaster.
        Preserves spatial dimensions across cells, only altering depth.
        ----------
        [Parameters]
        ----------
        channel_input: (integer.) channel size of input tensors.
        channel_upsample: (integer or list.) output channel sizes of upsample layers.
        channel_gru: (integer or list.) output channel size of gru cells.
        gru_link_size: (integer or list.) link size of subcnn layers in gru layers.
        upsample_k: (integer or list.) kernel size of upsample layers.
        upsample_s: (integer or list.) stride size of upsample layers.
        upsample_p: (integer or list.) padding size of upsample layers.
        gru_k: (integer or list.) kernel size of gru layers.
        gru_s: (integer or list.) stride size of gru layers.
        gru_p: (integer or list.) padding size of gru layers.
        n_cells: (integer.) number of chained "DeConvGRUcell".
        # output layer params
        channel_output: (integer or list.) output channel size of output layer.
        output_k: (integer or list.) kernel size of output layers.
        output_s: (integer or list.) stride size of output layers.
        output_p: (integer or list.) padding size of output layers.
        n_output_layers=1
        '''
        super().__init__()

        ## set self variables  
        self.device = device
        self.value_dtype = value_dtype
        self.channel_input = channel_input
    
        # channel size
        if type(channel_upsample) != list:
            channel_upsample = [channel_upsample]*n_cells
        assert len(channel_upsample) == n_cells, '"channel_upsample" must have the same length as n_cells'

        if type(channel_gru) != list:
            channel_gru = [channel_gru]*n_cells
        assert len(channel_gru) == n_cells, '"channel_gru" must have the same length as n_cells'

        if type(gru_link_size) != list:
            gru_link_size = [gru_link_size]*n_cells
        assert len(gru_link_size) == n_cells, '"gru_link_size" must have the same length as n_cells'
            
        # kernel size
        if type(upsample_k) != list:
            upsample_k = [upsample_k]*n_cells
        assert len(upsample_k) == n_cells, '"upsample_k" must have the same length as n_cells'
        # stride size
        if type(upsample_s) != list:
            upsample_s = [upsample_s]*n_cells
        assert len(upsample_s) == n_cells, '"upsample_s" must have the same length as n_cells'
        # padding size
        if type(upsample_p) != list:
            upsample_p = [upsample_p]*n_cells
        assert len(upsample_p) == n_cells, '"upsample_p" must have the same length as n_cells'


        if type(gru_k) != list:
            gru_k = [gru_k]*n_cells
        assert len(gru_k) == n_cells, '"gru_k" must have the same length as n_cells'

        if type(gru_s) != list:
            gru_s = [gru_s]*n_cells
        assert len(gru_s) == n_cells, '"gru_s" must have the same length as n_cells'

        if type(gru_p) != list:
            gru_p = [gru_p]*n_cells
        assert len(gru_p) == n_cells, '"gru_p" must have the same length as n_cells'

        # output size
        if type(channel_output) != list:
            channel_output = [channel_output]*int(n_output_layers)
        assert len(channel_output) == int(n_output_layers), '"channel_output" must have the same length as n_output_layers'

        if type(output_k) != list:
            output_k = [output_k]*int(n_output_layers)
        assert len(output_k) == int(n_output_layers), '"output_k" must have the same length as n_output_layers'

        if type(output_p) != list:
            output_p = [output_p]*int(n_output_layers)
        assert len(output_p) == int(n_output_layers), '"output_p" must have the same length as n_output_layers'

        if type(output_s) != list:
            output_s = [output_s]*int(n_output_layers)
        assert len(output_s) == int(n_output_layers), '"output_s" must have the same length as n_output_layers'

        self.n_output_layers = n_output_layers
        self.n_cells = n_cells

        ## set forecaster
        cells = []
        for i in range(n_cells):
            # deTraj gru
            if i == 0:
                cell = DeTrajGRUcell(channel_input=channel_input, channel_output=channel_gru[i], link_size=gru_link_size[i],
                                     kernel=gru_k[i], stride=gru_s[i], padding=gru_p[i], device=device, value_dtype=value_dtype)
            else:
                cell = DeTrajGRUcell(channel_input=channel_upsample[i-1], channel_output=channel_gru[i], link_size=gru_link_size[i],
                                     kernel=gru_k[i], stride=gru_s[i], padding=gru_p[i], device=device, value_dtype=value_dtype)

            name = 'DeTrajGRUcell_' + str(i).zfill(2)
            setattr(self, name, cell)
            cells.append(getattr(self, name))
            # decon  
            cell = DeCNN2D_cell(channel_gru[i], channel_upsample[i], upsample_k[i], upsample_s[i], upsample_p[i], batch_norm)
            name = 'Upsample_' + str(i).zfill(2)
            setattr(self, name, cell)
            cells.append(getattr(self, name))
        # output layer
        for i in range(self.n_output_layers):
            if i == 0:
                cell = CNN2D_cell(channel_upsample[-1], channel_output[i], output_k[i], output_s[i], output_p[i])
            else:
                cell = CNN2D_cell(channel_output[i-1], channel_output[i], output_k[i], output_s[i], output_p[i])
        
            name = 'OutputLayer_' + str(i).zfill(2)
            setattr(self, name, cell)
            cells.append(getattr(self, name))
        
        self.cells = cells

    def forward(self, hidden=None):
        '''
        Parameters
        ----------
        x : 4D input tensor. (batch, channels, height, width).
        hidden : list of 4D hidden state representations. (batch, channels, height, width).
        Returns
        -------
        upd_hidden : 5D hidden representation. (layer, batch, channels, height, width).
        '''

        upd_hidden = []
        output = 0

        for i in range(self.n_cells):
            if i == 0:
                ## Top gru cell in forecaster, no need the inputs
                cell = self.cells[2*i]
                cell_hidden = hidden[i]
                # pass through layer
                upd_cell_hidden = cell(prev_state=cell_hidden)
                upd_hidden.append(upd_cell_hidden)
            else:
                ## other gru cells in forecaster, need the inputs
                cell = self.cells[2*i]
                cell_hidden = hidden[i]
                # pass through layer
                upd_cell_hidden = cell(x=input_, prev_state=cell_hidden)
                upd_hidden.append(upd_cell_hidden)
            
            # update input_ to the last updated hidden layer for next pass
            input_ = upd_cell_hidden
            ## deconvolution
            cell = self.cells[2*i+1]
            input_ = cell(upd_cell_hidden)

        ## output layer
        cell = self.cells[-1]
        output = cell(input_)
        ## transfer rad to qpe
        output = ((10**(output/10))/200)**(5/8)
        # retain tensors in list to allow different hidden sizes
        return upd_hidden, output

class Multi_unit_Model(nn.Module):
    '''
        Argumensts:
            This class is used to construt multi-unit TrajGRU model based on given parameters.
        '''
    def __init__(self, n_encoders, n_forecasters, gru_link_size,
                encoder_input_channel, encoder_downsample_channels, encoder_gru_channels,
                encoder_downsample_k, encoder_downsample_s, encoder_downsample_p,
                encoder_gru_k, encoder_gru_s, encoder_gru_p, encoder_n_cells,
                forecaster_input_channel, forecaster_upsample_channels, forecaster_gru_channels,
                forecaster_upsample_k, forecaster_upsample_s, forecaster_upsample_p,
                forecaster_gru_k, forecaster_gru_s, forecaster_gru_p, forecaster_n_cells,
                forecaster_output=1, forecaster_output_k=1, forecaster_output_s=1, forecaster_output_p=0, forecaster_output_layers=1,
                batch_norm=False, device=None, value_dtype=None):

        super().__init__()
        self.n_encoders = n_encoders
        self.n_forecasters = n_forecasters
        self.name = 'Multi_unit_TRAJGRU'

        models = []
        # encoders
        for i in range(self.n_encoders):
            model = Encoder(channel_input=encoder_input_channel, channel_downsample=encoder_downsample_channels,
                            channel_gru=encoder_gru_channels, downsample_k=encoder_downsample_k, downsample_s=encoder_downsample_s, 
                            downsample_p=encoder_downsample_p, gru_link_size=gru_link_size, gru_k=encoder_gru_k, gru_s=encoder_gru_s, 
                            gru_p=encoder_gru_p, n_cells=encoder_n_cells, batch_norm=batch_norm, device=device, value_dtype=value_dtype)
            name = 'Encoder_' + str(i).zfill(2)
            setattr(self, name, model)
            models.append(getattr(self, name))

        # forecasters
        for i in range(self.n_forecasters):
            model = Forecaster(channel_input=forecaster_input_channel, channel_upsample=forecaster_upsample_channels, 
                               channel_gru=forecaster_gru_channels, upsample_k=forecaster_upsample_k, upsample_s=forecaster_upsample_s, 
                               upsample_p=forecaster_upsample_p, gru_link_size=gru_link_size, gru_k=forecaster_gru_k, 
                               gru_s=forecaster_gru_s, gru_p=forecaster_gru_p, n_cells=forecaster_n_cells,
                               channel_output=forecaster_output, output_k=forecaster_output_k, output_s=forecaster_output_s,
                               output_p=forecaster_output_p, n_output_layers=forecaster_output_layers, batch_norm=batch_norm, 
                               device=device, value_dtype=value_dtype)
            name = 'Forecaster_' + str(i).zfill(2)
            setattr(self, name, model)
            models.append(getattr(self, name))

        self.models = models

    def forward(self, x):
        input_ = x
        if input_.size()[1] != self.n_encoders:
            assert input_.size()[1] == self.n_encoders, '"x" must have the same as n_encoders'

        forecast = []

        for i in range(self.n_encoders):
            if i == 0:
                hidden=None
            model = self.models[i]
            hidden = model(x = input_[:,i,:,:,:], hidden=hidden)

        hidden = hidden[::-1]

        for i in range(self.n_forecasters):
            model = self.models[self.n_encoders+i]
            hidden, output = model(hidden=hidden)
            forecast.append(output)

        forecast = torch.cat(forecast, dim=1)
        return forecast


class Single_unit_Model(nn.Module):
    '''
        Argumensts:
            This class is used to construt single-unit TrajGRU model based on given parameters.
        '''
    def __init__(self, n_encoders, n_forecasters, gru_link_size,
                encoder_input_channel, encoder_downsample_channels, encoder_gru_channels,
                encoder_downsample_k, encoder_downsample_s, encoder_downsample_p,
                encoder_gru_k, encoder_gru_s, encoder_gru_p, encoder_n_cells,
                forecaster_input_channel, forecaster_upsample_channels, forecaster_gru_channels,
                forecaster_upsample_k, forecaster_upsample_s, forecaster_upsample_p,
                forecaster_gru_k, forecaster_gru_s, forecaster_gru_p, forecaster_n_cells,
                forecaster_output=1, forecaster_output_k=1, forecaster_output_s=1, forecaster_output_p=0, forecaster_output_layers=1,
                batch_norm=False, device=None, value_dtype=None):

        super().__init__()
        self.n_encoders = n_encoders
        self.n_forecasters = n_forecasters
        self.name = 'Single_unit_TRAJGRU'

        models = []
        # encoders
        self.encoder = Encoder(channel_input=encoder_input_channel, channel_downsample=encoder_downsample_channels,
                                channel_gru=encoder_gru_channels, downsample_k=encoder_downsample_k, downsample_s=encoder_downsample_s, 
                                downsample_p=encoder_downsample_p, gru_link_size=gru_link_size, gru_k=encoder_gru_k, gru_s=encoder_gru_s, 
                                gru_p=encoder_gru_p, n_cells=encoder_n_cells, batch_norm=batch_norm, device=device, value_dtype=value_dtype)

        # forecasters
        self.forecaster = Forecaster(channel_input=forecaster_input_channel, channel_upsample=forecaster_upsample_channels, 
                                    channel_gru=forecaster_gru_channels, upsample_k=forecaster_upsample_k, upsample_s=forecaster_upsample_s, 
                                    upsample_p=forecaster_upsample_p, gru_link_size=gru_link_size, gru_k=forecaster_gru_k, 
                                    gru_s=forecaster_gru_s, gru_p=forecaster_gru_p, n_cells=forecaster_n_cells,
                                    channel_output=forecaster_output, output_k=forecaster_output_k, output_s=forecaster_output_s,
                                    output_p=forecaster_output_p, n_output_layers=forecaster_output_layers, batch_norm=batch_norm, 
                                    device=device, value_dtype=value_dtype)

    def forward(self, x):
        input_ = x
        if input_.data.shape[1] != self.n_encoders:
            assert input_.data.shape[1] == self.n_encoders, '"x" must have the same as n_encoders'

        forecast = []

        for i in range(self.n_encoders):
            if i == 0:
                hidden=None
            hidden = self.encoder(x = input_[:,i,:,:,:], hidden=hidden)

        hidden = hidden[::-1]


        for i in range(self.n_forecasters):
            hidden, output = self.forecaster(hidden=hidden)
            forecast.append(output)
            
        forecast = torch.cat(forecast, dim=1)
        
        return forecast

    # def modify_value_dtype_(self, value_dtype=None):
    #     self.encoder.TrajGRUcell_00.value_dtype = value_dtype
    #     self.encoder.TrajGRUcell_01.value_dtype = value_dtype
    #     self.encoder.TrajGRUcell_02.value_dtype = value_dtype
    #     self.forecaster.DeTrajGRUcell_00.value_dtype = value_dtype
    #     self.forecaster.DeTrajGRUcell_01.value_dtype = value_dtype
    #     self.forecaster.DeTrajGRUcell_02.value_dtype = value_dtype
    #     self.encoder.TrajGRUcell_00.flow_warp.value_dtype = value_dtype
    #     self.encoder.TrajGRUcell_01.flow_warp.value_dtype = value_dtype
    #     self.encoder.TrajGRUcell_02.flow_warp.value_dtype = value_dtype
    #     self.forecaster.DeTrajGRUcell_00.flow_warp.value_dtype = value_dtype
    #     self.forecaster.DeTrajGRUcell_01.flow_warp.value_dtype = value_dtype
    #     self.forecaster.DeTrajGRUcell_02.flow_warp.value_dtype = value_dtype