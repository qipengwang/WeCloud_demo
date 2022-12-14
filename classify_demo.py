from torchvision.io import read_image
from torchvision.models import resnet50, ResNet50_Weights
from torchvision.models import vgg16_bn, VGG16_BN_Weights
from torchvision.models import mobilenet_v2, MobileNet_V2_Weights

def generate_model(model_type):
    '''
    Select model according to users' input
    '''
    if model_type == 'VGG':
        weights = VGG16_BN_Weights.DEFAULT
        model = vgg16_bn(weights=weights)
        model.eval()

    if model_type == 'ResNet':
        weights = ResNet50_Weights.DEFAULT
        model = resnet50(weights=weights)
        model.eval()

    if model_type == 'MobileNet':
        weights = MobileNet_V2_Weights.DEFAULT
        model = mobilenet_v2(weights=weights)
        model.eval()

    return model, weights


def classify_demo(imagepath):
    img = read_image(imagepath)

    # Step 1: Initialize model with the best available weights
    model, weights = generate_model('MobileNet')

    # Step 2: Initialize the inference transforms
    preprocess = weights.transforms()

    # Step 3: Apply inference preprocessing transforms
    batch = preprocess(img).unsqueeze(0)

    # Step 4: Use the model and print the predicted category
    prediction = model(batch).squeeze(0).softmax(0)
    class_id = prediction.argmax().item()
    score = prediction[class_id].item()
    category_name = weights.meta["categories"][class_id]
    print(f"{category_name}: {100 * score:.1f}%")
    return category_name, 100 * score


if __name__ == "__main__":
    classify_demo("test/cat.jpeg")